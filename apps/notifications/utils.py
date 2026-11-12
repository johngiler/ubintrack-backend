"""Helpers de presentación para notificaciones."""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model

from apps.core.utils.display_name import user_display_name

_EMAIL_RE = re.compile(r"^[^\s]+@[^\s]+$")
_LEADING_EMAIL_RE = re.compile(r"^([^\s]+@[^\s]+)\s+")


def _looks_like_email(value: str) -> bool:
    return bool(value and _EMAIL_RE.match(value.strip()))


def resolve_actor_display(stored: str) -> str:
    """Si `actor_display` es un correo, resolver al nombre del usuario."""
    raw = (stored or "").strip()
    if not raw:
        return ""
    if not _looks_like_email(raw):
        return raw

    User = get_user_model()
    user = (
        User.objects.filter(username__iexact=raw).first()
        or User.objects.filter(email__iexact=raw).first()
    )
    if user is not None:
        return user_display_name(user)

    local = raw.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
    return local.title() if local else raw


def strip_leading_actor(message: str, actor_display: str = "") -> str:
    """
    Quita el actor del inicio del mensaje (nombre o correo).
    La UI ya muestra el actor en una sublínea.
    """
    text = (message or "").strip()
    if not text:
        return ""

    match = _LEADING_EMAIL_RE.match(text)
    if match:
        text = text[match.end() :].lstrip()

    name = (actor_display or "").strip()
    if name and name != "Sistema":
        # Prefijo exacto del nombre visible (case-insensitive).
        prefix = re.compile(
            rf"^{re.escape(name)}\s+",
            re.IGNORECASE,
        )
        text = prefix.sub("", text, count=1).lstrip()

    if text:
        text = text[0].upper() + text[1:]
    return text
