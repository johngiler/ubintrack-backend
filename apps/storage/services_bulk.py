"""Runners de operaciones masivas de posiciones."""

from __future__ import annotations

from typing import Any

from apps.common.models import BulkOperationJob
from apps.common.utils.zebra import (
    USER_PRINT_FAILED,
    USER_PRINTER_NOT_CONFIGURED,
    printer_is_configured,
    print_to_zebra,
)
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
    from apps.storage.services import generate_position_multi_tag_zpl

    ids = job.item_ids or []
    params = (job.result or {}).get("params") or {}
    try:
        per_label = int(params.get("per_label") or 1)
    except (TypeError, ValueError):
        per_label = 1
    per_label = max(1, min(per_label, 4))
    same_copies = bool(params.get("same_position_copies"))

    # Conservar el orden de selección del job.
    by_id = {
        p.id: p
        for p in storage_models.Position.objects.filter(
            id__in=ids,
            cava__storage__workspace=job.workspace,
        ).only("id", "code", "level", "zpl_code")
    }
    ordered = [by_id[i] for i in ids if i in by_id]
    skipped = [p.code for p in ordered if not p.zpl_code]
    printable = [p for p in ordered if p.zpl_code]
    if not printable:
        raise RuntimeError("Ninguna posición tiene etiqueta para imprimir.")

    sheets: list[str] = []
    if same_copies:
        # N copias de la misma posición por etiqueta física.
        for position in printable:
            group = [position] * per_label
            zpl = generate_position_multi_tag_zpl(group)
            if zpl:
                sheets.append(zpl)
    else:
        # Posiciones distintas empaquetadas; última etiqueta puede quedar corta.
        for start in range(0, len(printable), per_label):
            group = printable[start : start + per_label]
            zpl = generate_position_multi_tag_zpl(group)
            if zpl:
                sheets.append(zpl)

    if not sheets:
        raise RuntimeError("Ninguna posición tiene etiqueta para imprimir.")
    if not printer_is_configured(job.workspace):
        raise RuntimeError(USER_PRINTER_NOT_CONFIGURED)
    if not print_to_zebra("\n".join(sheets), workspace=job.workspace):
        raise RuntimeError(USER_PRINT_FAILED)
    mode_label = "copias iguales" if same_copies else "distintas"
    parts = [
        f"Se enviaron {len(sheets)} etiquetas "
        f"({len(printable)} posiciones, {per_label} {mode_label} por etiqueta)."
    ]
    if skipped:
        parts.append(f"{len(skipped)} sin ZPL.")
    return {
        "printed": len(sheets),
        "positions": len(printable),
        "per_label": per_label,
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
