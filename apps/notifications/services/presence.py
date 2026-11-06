"""Presencia en línea por owner. Varias pestañas del mismo usuario cuentan como una."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import redis
from django.conf import settings

from apps.core.choices import UserType
from apps.core.models import CustomUser

logger = logging.getLogger(__name__)

STATUS_ACTIVE = "active"
STATUS_IDLE = "idle"
VALID_STATUSES = frozenset({STATUS_ACTIVE, STATUS_IDLE})


def presence_group(workspace_id: int) -> str:
    return f"presence_{int(workspace_id)}"


def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def _users_key(workspace_id: int) -> str:
    return f"ubintrack:presence:users:{int(workspace_id)}"


def _channels_key(workspace_id: int, user_id: int) -> str:
    return f"ubintrack:presence:channels:{int(workspace_id)}:{int(user_id)}"


def _status_key(workspace_id: int, user_id: int) -> str:
    return f"ubintrack:presence:status:{int(workspace_id)}:{int(user_id)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _aggregate_status(client: redis.Redis, workspace_id: int, user_id: int) -> str:
    values = client.hvals(_status_key(workspace_id, user_id))
    if any(value == STATUS_ACTIVE for value in values):
        return STATUS_ACTIVE
    if values:
        return STATUS_IDLE
    return STATUS_ACTIVE


def _role_label(user) -> str:
    if getattr(user, "is_superuser", False) or user.user_type == UserType.ADMINISTRATOR:
        return "Administrador"
    profile = getattr(user, "profile", None)
    if profile is not None and (profile.name or "").strip():
        return profile.name.strip()
    label = user.get_user_type_display() if user.user_type else ""
    return label or "Usuario"


def presence_user_payload(user, *, status: str = STATUS_ACTIVE, last_seen: str | None = None) -> dict[str, Any]:
    cleaned = status if status in VALID_STATUSES else STATUS_ACTIVE
    name = (getattr(user, "full_name", None) or "").strip() or user.get_username()
    return {
        "id": user.pk,
        "display_name": name,
        "username": user.get_username(),
        "role_label": _role_label(user),
        "status": cleaned,
        "last_seen": last_seen or _now_iso(),
    }


def _load_user(user_id: int):
    return CustomUser.objects.select_related("profile").filter(pk=user_id).first()


def list_online_users(workspace_id: int) -> list[dict[str, Any]]:
    try:
        raw = _redis().hgetall(_users_key(workspace_id))
    except redis.RedisError:
        logger.exception("No se pudo leer la presencia")
        return []
    users: list[dict[str, Any]] = []
    for value in raw.values():
        try:
            row = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            continue
        if "status" not in row:
            row["status"] = STATUS_ACTIVE
        users.append(row)
    users.sort(
        key=lambda row: (
            0 if row.get("status") == STATUS_ACTIVE else 1,
            (row.get("display_name") or "").lower(),
        )
    )
    return users


def register_presence(user, channel_name: str, workspace_id: int) -> tuple[bool, list[dict[str, Any]]]:
    user_id = int(user.pk)
    loaded = _load_user(user_id) or user
    client = _redis()
    try:
        added = client.sadd(_channels_key(workspace_id, user_id), channel_name)
        client.hset(_status_key(workspace_id, user_id), channel_name, STATUS_ACTIVE)
        status = _aggregate_status(client, workspace_id, user_id)
        payload = presence_user_payload(loaded, status=status, last_seen=_now_iso())
        client.hset(_users_key(workspace_id), str(user_id), json.dumps(payload))
    except redis.RedisError:
        logger.exception("No se pudo registrar la presencia")
        return False, list_online_users(workspace_id)
    return bool(added), list_online_users(workspace_id)


def unregister_presence(user_id: int, channel_name: str, workspace_id: int) -> tuple[bool, list[dict[str, Any]]]:
    client = _redis()
    channels = _channels_key(workspace_id, user_id)
    try:
        client.srem(channels, channel_name)
        client.hdel(_status_key(workspace_id, user_id), channel_name)
        remaining = client.scard(channels)
        if remaining == 0:
            client.delete(channels)
            client.delete(_status_key(workspace_id, user_id))
            client.hdel(_users_key(workspace_id), str(user_id))
            return True, list_online_users(workspace_id)
        raw = client.hget(_users_key(workspace_id), str(user_id))
        if raw:
            try:
                payload = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                payload = {"id": user_id}
            payload["status"] = _aggregate_status(client, workspace_id, user_id)
            payload["last_seen"] = _now_iso()
            client.hset(_users_key(workspace_id), str(user_id), json.dumps(payload))
    except redis.RedisError:
        logger.exception("No se pudo quitar la presencia")
        return False, list_online_users(workspace_id)
    return False, list_online_users(workspace_id)


def update_presence_status(
    user_id: int,
    channel_name: str,
    status: str,
    workspace_id: int,
) -> tuple[bool, list[dict[str, Any]]]:
    cleaned = (status or "").strip().lower()
    if cleaned not in VALID_STATUSES:
        return False, list_online_users(workspace_id)

    client = _redis()
    try:
        if not client.sismember(_channels_key(workspace_id, user_id), channel_name):
            return False, list_online_users(workspace_id)
        client.hset(_status_key(workspace_id, user_id), channel_name, cleaned)
        aggregated = _aggregate_status(client, workspace_id, user_id)
        raw = client.hget(_users_key(workspace_id), str(user_id))
        if not raw:
            return False, list_online_users(workspace_id)
        payload = json.loads(raw)
        previous = payload.get("status")
        payload["status"] = aggregated
        payload["last_seen"] = _now_iso()
        changed = previous != aggregated
        client.hset(_users_key(workspace_id), str(user_id), json.dumps(payload))
    except (redis.RedisError, TypeError, json.JSONDecodeError):
        logger.exception("No se pudo actualizar la presencia")
        return False, list_online_users(workspace_id)
    return changed, list_online_users(workspace_id)


def workspace_id_for_connection(user, scope) -> int | None:
    """Owner de esta conexión. No mezcla usuarios de otros owners."""
    from apps.workspaces.utils.tenant import workspace_from_http_headers

    headers: dict[str, str] = {}
    for key, value in scope.get("headers") or []:
        try:
            headers[key.decode("latin1").lower()] = value.decode("latin1")
        except UnicodeDecodeError:
            continue
    ws = workspace_from_http_headers(
        headers.get("host", ""),
        headers.get("origin", ""),
        headers.get("referer", ""),
    )
    user_workspace_id = getattr(user, "workspace_id", None)
    if ws is not None:
        if getattr(user, "is_superuser", False) or user_workspace_id == ws.id:
            return ws.id
        return None
    return user_workspace_id
