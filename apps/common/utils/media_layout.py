"""
Convención de rutas bajo ``MEDIA_ROOT`` (relativas al storage).

Multi-tenant (SaaS)
-------------------

Los medios quedan prefijados por el **slug del workspace** (tenant) para aislar en disco::

    <workspace_slug>/centers/… | <workspace_slug>/orders/… | …

El **branding del modelo ``Workspace``** (logos, favicon, PNG para correo/PDF) va bajo el
owner, mismo prefijo que centros y pedidos: ``<slug>/workspaces/logos|…``.

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
