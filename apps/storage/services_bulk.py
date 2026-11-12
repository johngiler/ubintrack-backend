"""Runners de operaciones masivas de posiciones."""

from __future__ import annotations

from typing import Any

from apps.common.models import BulkOperationJob
from apps.common.utils.zebra import print_to_zebra
from apps.storage import models as storage_models


def run_position_regenerate_tags(job: BulkOperationJob) -> dict[str, Any]:
    ids = job.item_ids or []
    qs = storage_models.Position.objects.filter(
        id__in=ids,
        cava__storage__workspace=job.workspace,
    )
    regenerated = 0
    failed = 0
    items: list[dict] = []
    for position in qs.iterator():
        try:
            position.refresh_label(persist=True)
            regenerated += 1
            items.append(
                {"resource_id": position.pk, "resource_code": position.code or ""}
            )
        except Exception:
            failed += 1
    missing = len(ids) - qs.count()
    parts = [f"Se regeneraron {regenerated} etiquetas."]
    if failed:
        parts.append(f"{failed} con error.")
    if missing:
        parts.append(f"{missing} no encontradas.")
    return {
        "regenerated": regenerated,
        "failed": failed,
        "items": items,
        "message": " ".join(parts),
    }


def run_position_print(job: BulkOperationJob) -> dict[str, Any]:
    ids = job.item_ids or []
    positions = list(
        storage_models.Position.objects.filter(
            id__in=ids,
            cava__storage__workspace=job.workspace,
        )
        .only("id", "code", "zpl_code")
        .order_by("cava__name", "hall", "rack", "level", "place")
    )
    skipped = [p.code for p in positions if not p.zpl_code]
    printable = [p for p in positions if p.zpl_code]
    zpl_jobs = [p.zpl_code for p in printable]
    if not zpl_jobs:
        raise RuntimeError("Ninguna posición tiene etiqueta para imprimir.")
    if not print_to_zebra("\n".join(zpl_jobs)):
        raise RuntimeError(
            "No se pudo imprimir la etiqueta. Verifique la impresora."
        )
    parts = [f"Se enviaron {len(zpl_jobs)} etiquetas."]
    if skipped:
        parts.append(f"{len(skipped)} sin ZPL.")
    return {
        "printed": len(zpl_jobs),
        "skipped": skipped,
        "items": [
            {"resource_id": p.pk, "resource_code": p.code or ""} for p in printable
        ],
        "message": " ".join(parts),
    }


def run_position_delete(job: BulkOperationJob) -> dict[str, Any]:
    ids = job.item_ids or []
    qs = storage_models.Position.objects.filter(
        id__in=ids,
        cava__storage__workspace=job.workspace,
    )
    found_ids = set(qs.values_list("id", flat=True))
    missing = len(ids) - len(found_ids)
    blocked_count = qs.exclude(status="libre").count()
    free_qs = qs.filter(status="libre")
    items = [
        {"resource_id": pk, "resource_code": code or ""}
        for pk, code in free_qs.values_list("id", "code")
    ]
    deleted_count, _ = free_qs.delete()
    parts = [f"Se eliminaron {deleted_count} posiciones."]
    if blocked_count:
        parts.append(f"{blocked_count} no estaban libres.")
    if missing:
        parts.append(f"{missing} no encontradas.")
    return {
        "deleted": deleted_count,
        "blocked": blocked_count,
        "items": items,
        "message": " ".join(parts),
    }
