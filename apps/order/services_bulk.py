"""Runners de operaciones masivas de órdenes de entrada/salida."""

from __future__ import annotations

from typing import Any

from apps.common.models import BulkOperationJob
from apps.order import models as order_models
from apps.order.services import update_entry_pdf, update_exit_pdf


def run_entry_regenerate_pdfs(job: BulkOperationJob) -> dict[str, Any]:
    ids = job.item_ids or []
    qs = (
        order_models.Entry.objects.filter(
            id__in=ids,
            client__workspace=job.workspace,
        )
        .select_related("client__workspace", "entry_detail", "storage")
        .prefetch_related("entry_products__product")
    )
    regenerated = 0
    skipped = 0
    failed_codes: list[str] = []
    items: list[dict] = []

    for entry in qs:
        if entry.status != "completada":
            skipped += 1
            continue
        try:
            detail, _ = order_models.EntryDetail.objects.get_or_create(entry=entry)
            pdf_path = update_entry_pdf(entry)
            if not pdf_path:
                failed_codes.append(entry.code or str(entry.pk))
                continue
            detail.pdf_document = pdf_path
            detail.save(update_fields=["pdf_document"])
            regenerated += 1
            items.append(
                {"resource_id": entry.pk, "resource_code": entry.code or ""}
            )
        except Exception:
            failed_codes.append(entry.code or str(entry.pk))

    missing = len(ids) - qs.count()
    parts = [f"Se regeneraron {regenerated} PDFs."]
    if skipped:
        parts.append(f"{skipped} omitidas (no completadas).")
    if failed_codes:
        parts.append(f"{len(failed_codes)} no se pudieron regenerar.")
    if missing:
        parts.append(f"{missing} no encontradas.")
    return {
        "regenerated": regenerated,
        "skipped": skipped,
        "failed": len(failed_codes),
        "failed_codes": failed_codes[:50],
        "items": items,
        "message": " ".join(parts),
    }


def run_exit_regenerate_pdfs(job: BulkOperationJob) -> dict[str, Any]:
    ids = job.item_ids or []
    qs = (
        order_models.Exit.objects.filter(
            id__in=ids,
            client__workspace=job.workspace,
        )
        .select_related("client__workspace", "exit_detail", "branch")
        .prefetch_related("exit_product_groups__product")
    )
    regenerated = 0
    skipped = 0
    failed_codes: list[str] = []
    items: list[dict] = []

    for exit_obj in qs:
        if exit_obj.status != "completada":
            skipped += 1
            continue
        try:
            detail, _ = order_models.ExitDetail.objects.get_or_create(exit=exit_obj)
            pdf_path = update_exit_pdf(exit_obj)
            if not pdf_path:
                failed_codes.append(exit_obj.code or str(exit_obj.pk))
                continue
            detail.pdf_document = pdf_path
            detail.save(update_fields=["pdf_document"])
            regenerated += 1
            items.append(
                {
                    "resource_id": exit_obj.pk,
                    "resource_code": exit_obj.code or "",
                }
            )
        except Exception:
            failed_codes.append(exit_obj.code or str(exit_obj.pk))

    missing = len(ids) - qs.count()
    parts = [f"Se regeneraron {regenerated} PDFs."]
    if skipped:
        parts.append(f"{skipped} omitidas (no completadas).")
    if failed_codes:
        parts.append(f"{len(failed_codes)} no se pudieron regenerar.")
    if missing:
        parts.append(f"{missing} no encontradas.")
    return {
        "regenerated": regenerated,
        "skipped": skipped,
        "failed": len(failed_codes),
        "failed_codes": failed_codes[:50],
        "items": items,
        "message": " ".join(parts),
    }


def run_entry_delete(job: BulkOperationJob) -> dict[str, Any]:
    """Elimina entradas; si alguna no es válida, no elimina ninguna."""
    from django.db import transaction

    ids = job.item_ids or []
    qs = list(
        order_models.Entry.objects.filter(
            id__in=ids,
            client__workspace=job.workspace,
        )
        .select_related("client")
        .prefetch_related("bins")
    )
    if len(qs) != len(set(ids)):
        raise ValueError("No se encontraron todas las entradas.")
    for entry in qs:
        if entry.status == "completada" or entry.bins.exists():
            raise ValueError(
                "Hay entradas que no se pueden eliminar (completadas o con bins)."
            )

    items: list[dict] = []
    with transaction.atomic():
        for entry in qs:
            items.append(
                {"resource_id": entry.pk, "resource_code": entry.code or ""}
            )
            entry.delete()

    deleted = len(items)
    return {
        "deleted": deleted,
        "blocked": 0,
        "items": items,
        "message": f"Se eliminaron {deleted} entradas.",
    }


def run_exit_delete(job: BulkOperationJob) -> dict[str, Any]:
    """Elimina salidas; si alguna no es válida, no elimina ninguna."""
    from django.db import transaction

    ids = job.item_ids or []
    qs = list(
        order_models.Exit.objects.filter(
            id__in=ids,
            client__workspace=job.workspace,
        )
        .select_related("client")
        .prefetch_related("exit_product_groups__exit_products")
    )
    if len(qs) != len(set(ids)):
        raise ValueError("No se encontraron todas las salidas.")
    for exit_obj in qs:
        if exit_obj.status == "completada":
            raise ValueError(
                "Hay salidas que no se pueden eliminar "
                "(completadas o con bins procesados)."
            )
        has_processed_bins = any(
            ep.is_processed
            for group in exit_obj.exit_product_groups.all()
            for ep in group.exit_products.all()
        )
        if has_processed_bins:
            raise ValueError(
                "Hay salidas que no se pueden eliminar "
                "(completadas o con bins procesados)."
            )

    items: list[dict] = []
    with transaction.atomic():
        for exit_obj in qs:
            items.append(
                {
                    "resource_id": exit_obj.pk,
                    "resource_code": exit_obj.code or "",
                }
            )
            exit_obj.delete()

    deleted = len(items)
    return {
        "deleted": deleted,
        "blocked": 0,
        "items": items,
        "message": f"Se eliminaron {deleted} salidas.",
    }
