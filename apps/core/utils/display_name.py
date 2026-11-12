"""Nombre visible de usuario (campanita, historiales, timelines). Nunca priorizar correo."""

from __future__ import annotations


def user_display_name(user) -> str:
    if user is None:
        return "Sistema"

    name = (getattr(user, "full_name", None) or "").strip()
    if name and "@" not in name:
        return name

    # AbstractUser first/last (por si algún flujo los llena).
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    combined = f"{first} {last}".strip()
    if combined and "@" not in combined:
        return combined

    # Último recurso: parte local del login, no el correo completo.
    for candidate in (
        getattr(user, "username", None),
        getattr(user, "email", None),
    ):
        raw = (candidate or "").strip()
        if not raw:
            continue
        if "@" in raw:
            local = (
                raw.split("@", 1)[0]
                .replace(".", " ")
                .replace("_", " ")
                .replace("-", " ")
                .strip()
            )
            if local:
                return local.title()
        elif raw:
            return raw

    return "Usuario"
