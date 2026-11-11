"""
Convención de rutas bajo ``MEDIA_ROOT`` (relativas al storage).

Multi-tenant (SaaS WMS)
-----------------------

Los medios quedan prefijados por el **slug del workspace** (owner) para aislar en disco::

    <workspace_slug>/inventory/… | <workspace_slug>/storage/… | <workspace_slug>/orders/…

El **branding del modelo ``Workspace``** (logos, favicon) va bajo::

    <slug>/workspaces/logos|logo_marks|favicons/YYYY/MM/
"""

from __future__ import annotations

import os

from django.apps import apps
from django.utils import timezone
from django.utils.text import get_valid_filename


def _safe_owner_slug_from_workspace(instance) -> str:
    """Segmento de ruta (slug del workspace) a partir del modelo ``Workspace`` (o None → unknown)."""
    if instance is None:
        return "unknown"
    slug = getattr(instance, "slug", None)
    s = (str(slug).strip().lower() if slug else "") or "unknown"
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in s)
    safe = safe.strip("-") or "unknown"
    return safe[:80]


def _join_under_workspace_slug(workspace_slug: str, *relative_parts: str, filename: str) -> str:
    ym = timezone.now().strftime("%Y/%m")
    fn = get_valid_filename(filename)
    return os.path.join(workspace_slug, *relative_parts, ym, fn)


def _workspace_brand_file_upload(instance, filename: str, subdir: str) -> str:
    slug = _safe_owner_slug_from_workspace(instance)
    return _join_under_workspace_slug(slug, "workspaces", subdir, filename=filename)


def workspace_brand_logo_upload(instance, filename: str) -> str:
    return _workspace_brand_file_upload(instance, filename, "logos")


def workspace_brand_logo_mark_upload(instance, filename: str) -> str:
    return _workspace_brand_file_upload(instance, filename, "logo_marks")


def workspace_brand_favicon_upload(instance, filename: str) -> str:
    return _workspace_brand_file_upload(instance, filename, "favicons")


# ---------------------------------------------------------------------------
# WMS Ubintrack — rutas bajo ``<workspace_slug>/…``
# ---------------------------------------------------------------------------


def safe_owner_slug(slug: str | None) -> str:
    """Slug sanitizado para segmento de carpeta (coherente con ``data_layout``)."""
    s = (str(slug).strip().lower() if slug else "") or "unknown"
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in s)
    safe = safe.strip("-") or "unknown"
    return safe[:80]


def _join_under_workspace_slug_stable(workspace_slug: str, *relative_parts: str, filename: str) -> str:
    """Ruta sin ``YYYY/MM`` — activos regenerables (tags, QR, PDFs por código)."""
    fn = get_valid_filename(filename)
    return os.path.join(workspace_slug, *relative_parts, fn)


def _workspace_from_client(client) -> object | None:
    ws = getattr(client, "workspace", None)
    if ws is None and getattr(client, "workspace_id", None):
        Workspace = apps.get_model("workspaces", "Workspace")
        ws = Workspace.objects.filter(pk=client.workspace_id).only("slug").first()
    return ws


def _workspace_from_bin(bin_instance) -> object | None:
    client = getattr(bin_instance, "client", None)
    if client is None and getattr(bin_instance, "client_id", None):
        Client = apps.get_model("core", "Client")
        client = (
            Client.objects.select_related("workspace")
            .filter(pk=bin_instance.client_id)
            .only("workspace__slug", "workspace_id")
            .first()
        )
    if client is None:
        entry = getattr(bin_instance, "entry", None)
        if entry is None and getattr(bin_instance, "entry_id", None):
            Entry = apps.get_model("order", "Entry")
            entry = (
                Entry.objects.select_related("client__workspace")
                .filter(pk=bin_instance.entry_id)
                .only("client__workspace__slug")
                .first()
            )
        if entry is not None:
            client = getattr(entry, "client", None)
    return _workspace_from_client(client) if client is not None else None


def _workspace_from_position(position) -> object | None:
    cava = getattr(position, "cava", None)
    if cava is None and getattr(position, "cava_id", None):
        Cava = apps.get_model("storage", "Cava")
        cava = (
            Cava.objects.select_related("storage__workspace")
            .filter(pk=position.cava_id)
            .only("storage__workspace__slug")
            .first()
        )
    if cava is None:
        return None
    storage = getattr(cava, "storage", None)
    if storage is None and getattr(cava, "storage_id", None):
        Storage = apps.get_model("storage", "Storage")
        storage = (
            Storage.objects.select_related("workspace")
            .filter(pk=cava.storage_id)
            .only("workspace__slug")
            .first()
        )
    ws = getattr(storage, "workspace", None) if storage is not None else None
    if ws is None and storage is not None and getattr(storage, "workspace_id", None):
        Workspace = apps.get_model("workspaces", "Workspace")
        ws = Workspace.objects.filter(pk=storage.workspace_id).only("slug").first()
    return ws


def _workspace_from_entry(entry) -> object | None:
    if entry is None:
        return None
    client = getattr(entry, "client", None)
    if client is None and getattr(entry, "client_id", None):
        Client = apps.get_model("core", "Client")
        client = (
            Client.objects.select_related("workspace")
            .filter(pk=entry.client_id)
            .only("workspace__slug")
            .first()
        )
    return _workspace_from_client(client) if client is not None else None


def _workspace_from_exit(exit_order) -> object | None:
    if exit_order is None:
        return None
    client = getattr(exit_order, "client", None)
    if client is None and getattr(exit_order, "client_id", None):
        Client = apps.get_model("core", "Client")
        client = (
            Client.objects.select_related("workspace")
            .filter(pk=exit_order.client_id)
            .only("workspace__slug")
            .first()
        )
    return _workspace_from_client(client) if client is not None else None


def _owner_slug_from_instance(instance, resolver) -> str:
    ws = resolver(instance)
    return _safe_owner_slug_from_workspace(ws)


def build_wms_bin_tag_relative(bin_instance) -> str:
    owner = _owner_slug_from_instance(bin_instance, _workspace_from_bin)
    code = getattr(bin_instance, "code", None) or "bin"
    return _join_under_workspace_slug_stable(
        owner, "inventory", "bins", "tags", filename=f"tag_{code}.webp"
    )


def build_wms_position_qr_relative(position) -> str:
    owner = _owner_slug_from_instance(position, _workspace_from_position)
    code = getattr(position, "code", None) or "position"
    return _join_under_workspace_slug_stable(
        owner, "storage", "positions", "qrcodes", filename=f"qrcode-{code}.webp"
    )


def build_wms_entry_pdf_relative(entry_instance) -> str:
    owner = _owner_slug_from_instance(entry_instance, _workspace_from_entry)
    safe_code = (getattr(entry_instance, "code", None) or "entry").replace("/", "_")
    return _join_under_workspace_slug_stable(
        owner, "orders", "entries", "documents", filename=f"{safe_code}.pdf"
    )


def build_wms_exit_pdf_relative(exit_instance) -> str:
    owner = _owner_slug_from_instance(exit_instance, _workspace_from_exit)
    safe_code = (getattr(exit_instance, "code", None) or "exit").replace("/", "_")
    return _join_under_workspace_slug_stable(
        owner, "orders", "exits", "documents", filename=f"{safe_code}.pdf"
    )


def wms_bin_tag_upload(instance, filename: str) -> str:
    return build_wms_bin_tag_relative(instance)


def wms_position_qr_upload(instance, filename: str) -> str:
    return build_wms_position_qr_relative(instance)


def wms_entry_detail_image_upload(instance, filename: str) -> str:
    entry = getattr(instance, "entry", None)
    owner = _owner_slug_from_instance(entry, _workspace_from_entry) if entry else "unknown"
    ext = os.path.splitext(filename)[1] or ".jpg"
    code = getattr(entry, "code", None) or get_valid_filename(filename)
    return _join_under_workspace_slug(
        owner, "orders", "entries", "images", filename=f"{code}{ext}"
    )


def wms_exit_detail_image_upload(instance, filename: str) -> str:
    exit_order = getattr(instance, "exit", None)
    owner = _owner_slug_from_instance(exit_order, _workspace_from_exit) if exit_order else "unknown"
    ext = os.path.splitext(filename)[1] or ".jpg"
    code = getattr(exit_order, "code", None) or get_valid_filename(filename)
    return _join_under_workspace_slug(
        owner, "orders", "exits", "images", filename=f"{code}{ext}"
    )


def wms_entry_pdf_upload(instance, filename: str) -> str:
    entry = getattr(instance, "entry", None)
    if entry is None:
        return build_wms_entry_pdf_relative(instance)
    return build_wms_entry_pdf_relative(entry)


def wms_exit_pdf_upload(instance, filename: str) -> str:
    exit_order = getattr(instance, "exit", None)
    if exit_order is None:
        return build_wms_exit_pdf_relative(instance)
    return build_wms_exit_pdf_relative(exit_order)


def media_url_with_bust(file_field, *, request=None, version=None) -> str | None:
    """URL absoluta del FileField con ``?t=`` para invalidar caché del navegador."""
    if not file_field:
        return None
    try:
        url = file_field.url
    except ValueError:
        return None
    if request is not None:
        url = request.build_absolute_uri(url)
    token = version
    if token is None:
        token = ""
    else:
        token = str(int(token.timestamp()) if hasattr(token, "timestamp") else token)
    if token:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}t={token}"
    return url


def ensure_media_parent_dir(relative_path: str) -> str:
    """Devuelve ruta absoluta; crea directorios padre si hace falta."""
    from django.conf import settings

    abs_path = os.path.join(settings.MEDIA_ROOT, relative_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    return abs_path


def is_tenant_scoped_media_path(relative_path: str | None) -> bool:
    """True si la ruta ya incluye slug de owner como primer segmento."""
    if not relative_path:
        return False
    normalized = relative_path.replace("\\", "/").lstrip("/")
    first = normalized.split("/", 1)[0]
    legacy_roots = {
        "tags",
        "positions",
        "documents",
        "entries",
        "exits",
    }
    return first not in legacy_roots and bool(first)
