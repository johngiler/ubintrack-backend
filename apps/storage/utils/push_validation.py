"""Validación de homogeneidad product_id + batch en conductos push."""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError

from apps.core import choices
from apps.inventory.models import Bin
from apps.storage.models import Position
from apps.storage.utils.conduits import conduit_filter_kwargs


@dataclass(frozen=True)
class ProductBatchKey:
    product_id: int
    batch: str


def bin_product_batch_key(bin_obj: Bin) -> ProductBatchKey | None:
    """Identidad de producto/lote del bin (primer BinProduct con entry_product)."""
    bp = (
        bin_obj.bin_products.select_related("entry_product__product")
        .filter(is_active=True)
        .order_by("id")
        .first()
    )
    if bp is None:
        # prefetch fallback
        products = list(bin_obj.bin_products.all())
        bp = next((x for x in products if getattr(x, "is_active", True)), None)
        if bp is None and products:
            bp = products[0]
    if bp is None or not bp.entry_product_id:
        return None
    ep = bp.entry_product
    if ep is None or not ep.product_id:
        return None
    batch = (ep.batch or bp.batch or "").strip()
    return ProductBatchKey(product_id=ep.product_id, batch=batch)


def conduit_occupied_product_batch(
    position: Position,
    *,
    exclude_bin_id: int | None = None,
) -> ProductBatchKey | None:
    """product_id+batch ya presente en el conducto (bins asignados/ocupados)."""
    conduit_kw = conduit_filter_kwargs(position)
    bins = (
        Bin.objects.filter(
            position__cava_id=conduit_kw["cava_id"],
            position__hall=conduit_kw["hall"],
            position__rack=conduit_kw["rack"],
            position__level=conduit_kw["level"],
            position__is_push=True,
            is_active=True,
            status__in=[
                choices.BinStatus.EN_RECIBO,
                choices.BinStatus.ALMACENADO,
                choices.BinStatus.EN_DESPACHO,
            ],
            position__isnull=False,
        )
        .exclude(status=choices.BinStatus.DESPACHADO)
        .select_related("position")
        .prefetch_related("bin_products__entry_product")
    )
    if exclude_bin_id:
        bins = bins.exclude(pk=exclude_bin_id)

    for bin_obj in bins:
        # Solo cuentan si la posición del conducto está asignada/ocupada
        if bin_obj.position and bin_obj.position.status == choices.PositionStatus.LIBRE:
            continue
        key = bin_product_batch_key(bin_obj)
        if key is not None:
            return key
    return None


def validate_push_assignment(
    position: Position,
    bin_obj: Bin,
    *,
    exclude_bin_id: int | None = None,
) -> None:
    """
    Si la posición es push, exige homogeneidad product_id+batch en el conducto.
    Lanza ValidationError con mensaje corto si falla.
    """
    if not position or not position.is_push:
        return

    incoming = bin_product_batch_key(bin_obj)
    if incoming is None:
        raise ValidationError("El bin no tiene producto/lote para validar el conducto push.")

    existing = conduit_occupied_product_batch(
        position, exclude_bin_id=exclude_bin_id or bin_obj.pk
    )
    if existing is None:
        return

    if existing != incoming:
        raise ValidationError(
            "El conducto push solo admite el mismo producto y lote."
        )
