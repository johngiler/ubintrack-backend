"""
Resolución de owner (Workspace) por petición y reglas de aislamiento SaaS WMS.

- API centralizado (api.ubintrack.com); tenant vía Origin/Referer o X-Workspace-Slug.
- Host: `{slug}.TENANT_BASE_DOMAIN` cuando la petición llega con ese Host.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from django.conf import settings

from apps.core import choices as core_choices
from apps.core.utils.user_roles import get_user_client, is_client_user
from apps.workspaces.models import Workspace

RESERVED_SUBDOMAINS = frozenset({"www", "api", "cdn"})


def _slug_from_host(host: str, base_domain: str) -> str | None:
    if not host or not base_domain:
        return None
    host = host.lower().split(":")[0]
    base = base_domain.lower().strip().strip(".")
    if not base:
        return None
    if host == base:
        return None
    suffix = f".{base}"
    if not host.endswith(suffix):
        return None
    sub = host[: -len(suffix)]
    if not sub or "." in sub:
        return None
    if sub in RESERVED_SUBDOMAINS:
        return None
    return sub


def _slug_from_url(url: str, base_domain: str) -> str | None:
    if not url or not base_domain:
        return None
    try:
        parsed = urlparse(url)
        h = (parsed.hostname or "").lower()
    except ValueError:
        return None
    return _slug_from_host(h, base_domain)


def _tenant_apex_for_resolution() -> str:
    base = getattr(settings, "TENANT_BASE_DOMAIN", "").strip()
    if base:
        return base
    if getattr(settings, "DEBUG", False):
        return "localhost"
    return ""


def resolve_request_workspace(request) -> tuple[Workspace | None, str | None]:
    """
    Devuelve (workspace, error_code).
    error_code: 'unknown_slug' si se infirió un slug explícito que no existe en BD.
    """
    header_slug = (
        request.META.get("HTTP_X_WORKSPACE_SLUG")
        or request.META.get("HTTP_X_TENANT_SLUG")
        or ""
    ).strip().lower()
    if header_slug and header_slug not in RESERVED_SUBDOMAINS:
        ws = Workspace.objects.filter(slug=header_slug, is_active=True).first()
        if ws is None:
            return None, "unknown_slug"
        return ws, None

    base = _tenant_apex_for_resolution()
    if not base:
        return get_default_workspace_safely(), None

    host = request.get_host().split(":")[0].lower()
    slug = _slug_from_host(host, base)

    if slug is None:
        origin = request.META.get("HTTP_ORIGIN", "")
        slug = _slug_from_url(origin, base)
    if slug is None:
        ref = request.META.get("HTTP_REFERER", "")
        slug = _slug_from_url(ref, base)

    if slug:
        ws = Workspace.objects.filter(slug=slug, is_active=True).first()
        if ws is None:
            return None, "unknown_slug"
        return ws, None

    return get_default_workspace_safely(), None


def default_workspace_slug() -> str:
    raw = getattr(settings, "DEFAULT_WORKSPACE_SLUG", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    return "rbcold"


def get_default_workspace_safely() -> Workspace | None:
    slug = default_workspace_slug()
    ws = Workspace.objects.filter(slug=slug, is_active=True).first()
    if ws:
        return ws
    return Workspace.objects.filter(is_active=True).order_by("id").first()


def spa_public_base_url(ws: Workspace | None) -> str:
    slug = ""
    if ws is not None and (getattr(ws, "slug", None) or "").strip():
        slug = str(ws.slug).strip().lower()
    else:
        slug = default_workspace_slug()

    apex = _tenant_apex_for_resolution()
    if apex:
        if apex == "localhost":
            port = (os.environ.get("FRONTEND_DEV_PORT") or "3000").strip() or "3000"
            return f"http://{slug}.localhost:{port}".rstrip("/")
        scheme = (os.environ.get("FRONTEND_URL_SCHEME") or "https").strip().lower()
        if scheme not in ("http", "https"):
            scheme = "https"
        return f"{scheme}://{slug}.{apex}".rstrip("/")

    fb = (getattr(settings, "FRONTEND_BASE_URL", None) or "").strip().rstrip("/")
    if fb:
        return fb
    return f"http://{slug}.localhost:3000".rstrip("/")


def get_workspace_for_request(request) -> Workspace | None:
    if request is not None:
        ws = getattr(request, "workspace", None)
        if ws is not None:
            return ws
        err = getattr(request, "workspace_resolution_error", None)
        if err == "unknown_slug":
            return None
    return get_default_workspace_safely()


def is_platform_staff(user) -> bool:
    """Staff de plataforma Django (superuser o is_staff sin workspace)."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.is_staff and getattr(user, "workspace_id", None) is None:
        return True
    return False


def is_platform_only_user(user) -> bool:
    """Usuario reservado para Django admin (sin owner asociado)."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return bool(user.is_staff and getattr(user, "workspace_id", None) is None)


def user_can_login_frontend(user, workspace: Workspace | None) -> bool:
    """
    Login al frontend WMS: solo usuarios ligados a un owner.
    Superusers/staff de plataforma (createsuperuser) van al Django admin.
    """
    if not user or not getattr(user, "is_active", False) or workspace is None:
        return False
    if is_platform_only_user(user):
        return False

    user_type = getattr(user, "user_type", None)

    if user_type in (core_choices.UserType.ADMINISTRATOR, core_choices.UserType.EMPLOYEE):
        return getattr(user, "workspace_id", None) == workspace.id

    if user_type == core_choices.UserType.CLIENT or is_client_user(user):
        client = get_user_client(user)
        if client and client.workspace_id:
            return client.workspace_id == workspace.id

    return False


def user_can_access_workspace(user, workspace: Workspace) -> bool:
    """¿Puede este usuario operar en el contexto de este owner (API autenticada)?"""
    if not user or not user.is_authenticated or workspace is None:
        return False
    if is_platform_staff(user):
        return True

    user_type = getattr(user, "user_type", None)

    if user_type in (core_choices.UserType.ADMINISTRATOR, core_choices.UserType.EMPLOYEE):
        return getattr(user, "workspace_id", None) == workspace.id

    if user_type == core_choices.UserType.CLIENT or is_client_user(user):
        client = get_user_client(user)
        if client and client.workspace_id:
            return client.workspace_id == workspace.id

    return False


def enforce_workspace_for_non_superuser(request, explicit_workspace: Workspace | None) -> Workspace:
    from rest_framework.exceptions import ValidationError

    if is_platform_staff(getattr(request, "user", None)):
        req_ws = get_workspace_for_request(request) or explicit_workspace
        if req_ws is None:
            raise ValidationError({"workspace": "No se pudo determinar el owner de esta petición."})
        return req_ws

    req_ws = get_workspace_for_request(request)
    if req_ws is None:
        raise ValidationError(
            {"workspace": "No se pudo determinar el owner de esta petición (Host u Origin)."}
        )
    if explicit_workspace is not None and explicit_workspace.id != req_ws.id:
        raise ValidationError({"workspace": "No puedes crear o asignar recursos de otro owner."})
    return req_ws
