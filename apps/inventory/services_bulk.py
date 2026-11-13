"""Runners de operaciones masivas de bins."""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.common.models import BulkOperationJob
from apps.common.utils.zebra import (
    USER_PRINT_FAILED,
    USER_PRINTER_NOT_CONFIGURED,
    printer_is_configured,
    print_zpl_sheets,
)
from apps.core import choices
from apps.inventory import models as inventory_models
from apps.order.models import ExitProduct
from apps.storage.models import Position


def run_bin_regenerate_tags(job: BulkOperationJob) -> dict[str, Any]:
    ids = job.item_ids or []
    qs = (
        inventory_models.Bin.objects.filter(id__in=ids, workspace=job.workspace)
        .select_related("client", "client__workspace", "position")
        .prefetch_related("bin_products__entry_product__product")
    )
    regenerated = 0
    failed_codes: list[str] = []
    items: list[dict] = []
    # Lista materializada: el prefetch no aplica bien con iterator().
    for bin_obj in qs:
        if bin_obj.generate_tag():
            regenerated += 1
            items.append(
                {"resource_id": bin_obj.pk, "resource_code": bin_obj.code or ""}
            )
        else:
            failed_codes.append(bin_obj.code)
    missing = len(ids) - qs.count()
    parts = [f"Se regeneraron {regenerated} etiquetas."]
    if failed_codes:
        parts.append(f"{len(failed_codes)} no se pudieron regenerar.")
    if missing:
        parts.append(f"{missing} no encontrados.")
    return {
        "regenerated": regenerated,
        "failed": len(failed_codes),
        "failed_codes": failed_codes[:50],
        "items": items,
        "message": " ".join(parts),
    }


def run_bin_print(job: BulkOperationJob) -> dict[str, Any]:
    from apps.inventory.services import generate_bin_tag_zpl

    ids = job.item_ids or []
    bins = list(
        inventory_models.Bin.objects.filter(id__in=ids, workspace=job.workspace)
        .select_related("client", "position", "workspace")
        .prefetch_related("bin_products__entry_product__product")
        .order_by("code")
    )
    skipped = [b.code for b in bins if not b.code]
    printable = [b for b in bins if b.code]
    zpl_jobs = []
    for bin_obj in printable:
        zpl = generate_bin_tag_zpl(bin_obj)
        if zpl:
            zpl_jobs.append(zpl)
        else:
            skipped.append(bin_obj.code)
    if not zpl_jobs:
        raise RuntimeError("Ningún bin tiene etiqueta para imprimir.")
    if not printer_is_configured(job.workspace):
        raise RuntimeError(USER_PRINTER_NOT_CONFIGURED)
    if not print_zpl_sheets(zpl_jobs, workspace=job.workspace):
        raise RuntimeError(USER_PRINT_FAILED)
    parts = [f"Se enviaron {len(zpl_jobs)} etiquetas."]
    if skipped:
        parts.append(f"{len(skipped)} sin ZPL.")
    return {
        "printed": len(zpl_jobs),
        "skipped": skipped,
        "items": [
            {"resource_id": b.pk, "resource_code": b.code or ""} for b in printable
        ],
        "message": " ".join(parts),
    }


def run_bin_delete(job: BulkOperationJob) -> dict[str, Any]:
    ids = job.item_ids or []
    qs = inventory_models.Bin.objects.filter(id__in=ids, workspace=job.workspace)
    found_ids = set(qs.values_list("id", flat=True))
    missing = len(ids) - len(found_ids)
    blocked = 0
    deleted = 0
    items: list[dict] = []
    for bin_obj in qs.iterator():
        if bin_obj.status == choices.BinStatus.ALMACENADO:
            has_exit_products = ExitProduct.objects.filter(
                bin_product__bin=bin_obj,
                exit_product_group__exit__status__in=["pendiente", "en_progreso"],
            ).exists()
            if has_exit_products:
                blocked += 1
                continue
        if bin_obj.position_id:
            Position.objects.filter(pk=bin_obj.position_id).update(
                status=choices.PositionStatus.LIBRE,
                updated_at=timezone.now(),
            )
        items.append({"resource_id": bin_obj.pk, "resource_code": bin_obj.code or ""})
        bin_obj.delete()
        deleted += 1
    parts = [f"Se eliminaron {deleted} bins."]
    if blocked:
        parts.append(f"{blocked} con salidas asociadas.")
    if missing:
        parts.append(f"{missing} no encontrados.")
    return {
        "deleted": deleted,
        "items": items,
        "blocked": blocked,
        "message": " ".join(parts),
    }
