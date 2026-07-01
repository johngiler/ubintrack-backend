"""
Convención de rutas bajo ``MEDIA_ROOT`` (relativas al storage).

Multi-tenant (SaaS)
-------------------

Los medios quedan prefijados por el **slug del workspace** (tenant) para aislar en disco::

    <workspace_slug>/inventory/… | <workspace_slug>/storage/… | <workspace_slug>/orders/…

El **branding del modelo ``Workspace``** (logos, favicon) va bajo::

    <slug>/workspaces/logos|logo_marks|favicons/YYYY/MM/

No se usa un segmento tipo ``owners/``.

Los slugs en BD son ``[a-z0-9-]+``; igual sanitizamos el segmento de carpeta.

**Subidas nuevas** usan estos ``upload_to``. Los registros antiguos pueden conservar
rutas previas (``centers/covers/…``, ``orders/receipts/…``, etc.) hasta una migración
física opcional.
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


def workspace_brand_logo_png_artifacts_upload(instance, filename: str) -> str:
    return _workspace_brand_file_upload(instance, filename, "logo_png_artifacts")


def workspace_brand_signature_png_upload(instance, filename: str) -> str:
    return _workspace_brand_file_upload(instance, filename, "signatures")


def workspace_brand_stamp_png_upload(instance, filename: str) -> str:
    return _workspace_brand_file_upload(instance, filename, "stamps")


def _workspace_from_shopping_center(instance) -> object | None:
    ws = getattr(instance, "workspace", None)
    if ws is None and getattr(instance, "workspace_id", None):
        Workspace = apps.get_model("workspaces", "Workspace")
        ws = Workspace.objects.filter(pk=instance.workspace_id).only("slug").first()
    return ws


def shopping_center_cover_upload(instance, filename: str) -> str:
    ws = _workspace_from_shopping_center(instance)
    owner = _safe_owner_slug_from_workspace(ws)
    return _join_under_workspace_slug(owner, "centers", "covers", filename=filename)


def _workspace_from_ad_space(ad) -> object | None:
    sc = getattr(ad, "shopping_center", None)
    if sc is None and getattr(ad, "shopping_center_id", None):
        ShoppingCenter = apps.get_model("malls", "ShoppingCenter")
        sc = (
            ShoppingCenter.objects.select_related("workspace")
            .filter(pk=ad.shopping_center_id)
            .only("workspace_id", "workspace__slug")
            .first()
        )
    if sc is None:
        return None
    ws = getattr(sc, "workspace", None)
    if ws is None and getattr(sc, "workspace_id", None):
        Workspace = apps.get_model("workspaces", "Workspace")
        ws = Workspace.objects.filter(pk=sc.workspace_id).only("slug").first()
    return ws


def ad_space_cover_upload(instance, filename: str) -> str:
    ws = _workspace_from_ad_space(instance)
    owner = _safe_owner_slug_from_workspace(ws)
    return _join_under_workspace_slug(owner, "spaces", "covers", filename=filename)


def _workspace_from_ad_space_image(img) -> object | None:
    sp = getattr(img, "ad_space", None)
    if sp is None and getattr(img, "ad_space_id", None):
        AdSpace = apps.get_model("ad_spaces", "AdSpace")
        sp = AdSpace.objects.filter(pk=img.ad_space_id).only("shopping_center_id").first()
    if sp is None:
        return None
    return _workspace_from_ad_space(sp)


def ad_space_gallery_upload(instance, filename: str) -> str:
    ws = _workspace_from_ad_space_image(instance)
    owner = _safe_owner_slug_from_workspace(ws)
    return _join_under_workspace_slug(owner, "spaces", "gallery", filename=filename)


def ad_space_location_image_upload(instance, filename: str) -> str:
    ws = _workspace_from_ad_space_image(instance)
    owner = _safe_owner_slug_from_workspace(ws)
    return _join_under_workspace_slug(owner, "spaces", "location", filename=filename)


def ad_space_production_image_upload(instance, filename: str) -> str:
    ws = _workspace_from_ad_space_image(instance)
    owner = _safe_owner_slug_from_workspace(ws)
    return _join_under_workspace_slug(owner, "spaces", "production", filename=filename)


def _workspace_from_client(client) -> object | None:
    ws = getattr(client, "workspace", None)
    if ws is None and getattr(client, "workspace_id", None):
        Workspace = apps.get_model("workspaces", "Workspace")
        ws = Workspace.objects.filter(pk=client.workspace_id).only("slug").first()
    return ws


def client_cover_upload(instance, filename: str) -> str:
    ws = _workspace_from_client(instance)
    owner = _safe_owner_slug_from_workspace(ws)
    return _join_under_workspace_slug(owner, "clients", "covers", filename=filename)


def _workspace_from_client_brand(instance) -> object | None:
    client = getattr(instance, "client", None)
    if client is None and getattr(instance, "client_id", None):
        Client = apps.get_model("clients", "Client")
        client = (
            Client.objects.select_related("workspace")
            .filter(pk=instance.client_id)
            .only("workspace__slug")
            .first()
        )
    return _workspace_from_client(client) if client is not None else None


def client_brand_logo_upload(instance, filename: str) -> str:
    ws = _workspace_from_client_brand(instance)
    owner = _safe_owner_slug_from_workspace(ws)
    return _join_under_workspace_slug(owner, "clients", "brands", filename=filename)


def user_profile_cover_upload(instance, filename: str) -> str:
    ws = getattr(instance, "workspace", None)
    if ws is None and getattr(instance, "workspace_id", None):
        Workspace = apps.get_model("workspaces", "Workspace")
        ws = Workspace.objects.filter(pk=instance.workspace_id).only("slug").first()
    if ws is None:
        cl = getattr(instance, "client", None)
        if cl is None and getattr(instance, "client_id", None):
            Client = apps.get_model("clients", "Client")
            cl = (
                Client.objects.select_related("workspace")
                .filter(pk=instance.client_id)
                .only("workspace__slug")
                .first()
            )
        if cl is not None:
            ws = getattr(cl, "workspace", None)
    owner = _safe_owner_slug_from_workspace(ws)
    return _join_under_workspace_slug(owner, "users", "covers", filename=filename)


def _workspace_from_order(order) -> object | None:
    if order is None:
        return None
    cl = getattr(order, "client", None)
    if cl is None and getattr(order, "client_id", None):
        Client = apps.get_model("clients", "Client")
        cl = (
            Client.objects.select_related("workspace")
            .filter(pk=order.client_id)
            .only("workspace__slug", "workspace_id")
            .first()
        )
    if cl is None:
        return None
    return _workspace_from_client(cl)


def _order_file_upload(instance, filename: str, *path_segments: str) -> str:
    ws = _workspace_from_order(instance)
    owner = _safe_owner_slug_from_workspace(ws)
    return _join_under_workspace_slug(owner, *path_segments, filename=filename)


def order_payment_receipt_upload(instance, filename: str) -> str:
    return _order_file_upload(instance, filename, "orders", "receipts")


def order_generated_document_upload(instance, filename: str) -> str:
    """PDFs generados (negociación, alcaldía, factura)."""
    return _order_file_upload(instance, filename, "orders", "generated")


def order_invoice_digital_upload(instance, filename: str) -> str:
    """Factura digital externa subida por el admin (PDF o imagen)."""
    return _order_file_upload(instance, filename, "orders", "invoices-external")


def _workspace_from_payment_installment(instance):
    plan = getattr(instance, "plan", None)
    if plan is None:
        pid = getattr(instance, "plan_id", None)
        if pid:
            OrderPaymentPlan = apps.get_model("orders", "OrderPaymentPlan")
            plan = (
                OrderPaymentPlan.objects.filter(pk=pid)
                .select_related("order__client__workspace")
                .first()
            )
    order = getattr(plan, "order", None) if plan else None
    return _workspace_from_order(order)


def _payment_installment_file_upload(instance, filename: str, *path_segments: str) -> str:
    ws = _workspace_from_payment_installment(instance)
    owner = _safe_owner_slug_from_workspace(ws)
    return _join_under_workspace_slug(owner, *path_segments, filename=filename)


def order_payment_installment_generated_upload(instance, filename: str) -> str:
    return _payment_installment_file_upload(
        instance, filename, "orders", "installments", "generated"
    )


def order_payment_installment_invoice_digital_upload(instance, filename: str) -> str:
    return _payment_installment_file_upload(
        instance, filename, "orders", "installments", "invoices-external"
    )


def order_payment_installment_receipt_upload(instance, filename: str) -> str:
    return _payment_installment_file_upload(
        instance, filename, "orders", "installments", "receipts"
    )


def order_signed_document_upload(instance, filename: str) -> str:
    return _order_file_upload(instance, filename, "orders", "signed")


def _workspace_from_order_fk(instance, order_attr: str = "order"):
    order = getattr(instance, order_attr, None)
    if order is None:
        oid = getattr(instance, f"{order_attr}_id", None)
        if oid:
            Order = apps.get_model("orders", "Order")
            order = Order.objects.filter(pk=oid).only("client_id").first()
    return _workspace_from_order(order)


def _order_attachment_file_upload(instance, filename: str, *path_segments: str) -> str:
    ws = _workspace_from_order_fk(instance, "order")
    owner = _safe_owner_slug_from_workspace(ws)
    return _join_under_workspace_slug(owner, *path_segments, filename=filename)


def order_art_attachment_upload(instance, filename: str) -> str:
    return _order_attachment_file_upload(instance, filename, "orders", "arts")


def order_installation_permit_pdf_upload(instance, filename: str) -> str:
    return _order_attachment_file_upload(
        instance, filename, "orders", "installation_permits"
    )


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
        "centers",
        "orders",
        "spaces",
        "clients",
        "users",
    }
    return first not in legacy_roots and bool(first)
