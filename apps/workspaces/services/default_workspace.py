"""
Workspace placeholder (p. ej. acme) para instalación vacía y desarrollo.
"""

from __future__ import annotations

from django.conf import settings

from apps.workspaces.models import Workspace
from apps.workspaces.tenant import default_workspace_slug

# Valores por defecto del owner dummy; no confundir con tenants reales (sambil, nobis, …).
PLACEHOLDER_DEFAULTS: dict[str, object] = {
    "name": "Acme",
    "legal_name": "Acme Demo, C.A.",
    "marketplace_title": "Acme Marketplace",
    "marketplace_tagline": "Espacios publicitarios de demostración.",
    "primary_color": "#2c2c81",
    "secondary_color": "#ea580c",
    "country": "",
    "city": "",
    "support_email": "",
    "phone": "",
    "is_active": True,
    "can_create_shopping_centers": True,
    "can_create_ad_spaces": True,
    "can_create_marketplace_admin_users": True,
}

_TEXT_FIELDS = (
    "name",
    "legal_name",
    "marketplace_title",
    "marketplace_tagline",
    "primary_color",
    "secondary_color",
    "country",
    "city",
    "support_email",
    "phone",
)


def resolve_placeholder_slug(slug: str | None = None) -> str:
    raw = (slug or "").strip().lower()
    if raw:
        return raw
    return default_workspace_slug()


def ensure_default_workspace(
    slug: str | None = None,
    *,
    fill_missing: bool = True,
) -> tuple[Workspace, bool]:
    """
    Crea el workspace placeholder si no existe.

    Si ya existe y ``fill_missing`` es True, rellena solo campos de texto vacíos
    con ``PLACEHOLDER_DEFAULTS`` (no pisa branding ya configurado).
    """
    resolved = resolve_placeholder_slug(slug)
    defaults = {**PLACEHOLDER_DEFAULTS, "slug": resolved}

    ws, created = Workspace.objects.get_or_create(
        slug=resolved,
        defaults=defaults,
    )

    if created:
        return ws, True

    if not fill_missing:
        return ws, False

    update_fields: list[str] = []
    for field in _TEXT_FIELDS:
        current = (getattr(ws, field, None) or "").strip()
        if current:
            continue
        new_val = PLACEHOLDER_DEFAULTS.get(field, "")
        if new_val != getattr(ws, field):
            setattr(ws, field, new_val)
            update_fields.append(field)

    if not ws.is_active:
        ws.is_active = True
        update_fields.append("is_active")

    if update_fields:
        ws.save(update_fields=update_fields)

    return ws, False


def expected_placeholder_slug() -> str:
    """Slug configurado en settings (referencia para mensajes)."""
    return (
        getattr(settings, "DEFAULT_WORKSPACE_SLUG", None) or default_workspace_slug()
    ).strip().lower() or default_workspace_slug()
