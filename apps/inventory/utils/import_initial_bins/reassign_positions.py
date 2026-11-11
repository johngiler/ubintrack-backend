"""Reasigna posiciones de bins del import CDF tras el remap Excel C1 → Cámara 9."""

from __future__ import annotations

import re
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core import choices
from apps.inventory.models import Bin
from apps.storage.models import Position
from apps.workspaces.models import Workspace

from .positions import map_excel_position

IMPORT_MARKER = "Importado desde archivo Inventario Inicial CDF.xlsx"
EXCEL_POS_RE = re.compile(r"Posición Excel\s+(\S+)", re.I)


def _excel_position_from_observation(observation: str) -> str | None:
    match = EXCEL_POS_RE.search(observation or "")
    if not match:
        return None
    return match.group(1).strip().upper()


def _rewrite_observation(
    observation: str,
    *,
    excel: str,
    mapped_code: str | None,
    position_found: bool,
    map_note: str,
) -> str:
    lines = [line for line in (observation or "").splitlines() if line.strip()]
    # Quitar líneas viejas de posición / remap.
    kept = []
    for line in lines:
        low = line.lower()
        if low.startswith("posición excel"):
            continue
        if "cava excel c1" in low:
            continue
        if "mapeada a" in low and "sin asignar" in low:
            continue
        if "→" in line and "c1-" in low and "posición" in low:
            continue
        kept.append(line)

    if not kept or not kept[0].startswith("Importado desde archivo"):
        kept.insert(0, IMPORT_MARKER)

    if mapped_code and position_found:
        kept.insert(1, map_note or f"Posición Excel {excel} → {mapped_code}")
    elif mapped_code:
        kept.insert(
            1,
            f"Posición Excel {excel} mapeada a {mapped_code} "
            "pero no existe en el owner (sin asignar)",
        )
    elif excel:
        kept.insert(1, map_note or f"Posición Excel {excel} sin equivalente")

    # Idempotencia: una sola nota de corrección C1→9
    note_c9 = "Corrección: Excel C1 = Cámara 9 (no Andén)"
    kept = [line for line in kept if line != note_c9]
    kept.insert(1, note_c9)
    return "\n".join(kept)


@transaction.atomic
def reassign_cdf_bins_to_camara_9(*, workspace_slug: str = "coldcave") -> dict[str, Any]:
    workspace = Workspace.objects.get(slug=workspace_slug)
    bins = list(
        Bin.objects.filter(
            workspace=workspace,
            observation__startswith=IMPORT_MARKER,
        ).select_related("position")
    )
    positions = {
        code: pk
        for code, pk in Position.objects.filter(
            cava__storage__workspace=workspace
        ).values_list("code", "id")
    }

    report = {
        "bins": len(bins),
        "assigned": 0,
        "cleared": 0,
        "unchanged": 0,
        "still_unmapped": 0,
        "anden_freed": 0,
    }

    old_anden_ids: set[int] = set()
    new_occupied_ids: set[int] = set()

    for bin_obj in bins:
        excel = _excel_position_from_observation(bin_obj.observation or "")
        if not excel:
            report["unchanged"] += 1
            continue

        mapped = map_excel_position(excel)
        code = mapped.get("mapped_code")
        position_id = positions.get(code) if code else None

        old_position_id = bin_obj.position_id
        if old_position_id and bin_obj.position and str(bin_obj.position.code).startswith("C1-"):
            old_anden_ids.add(old_position_id)

        observation = _rewrite_observation(
            bin_obj.observation or "",
            excel=excel,
            mapped_code=code,
            position_found=bool(position_id),
            map_note=mapped.get("note") or "",
        )

        if position_id:
            Bin.objects.filter(pk=bin_obj.pk).update(
                position_id=position_id,
                status=choices.BinStatus.ALMACENADO,
                observation=observation,
                updated_at=timezone.now(),
            )
            new_occupied_ids.add(position_id)
            report["assigned"] += 1
            if old_position_id and old_position_id != position_id:
                report["cleared"] += 1
        else:
            Bin.objects.filter(pk=bin_obj.pk).update(
                position_id=None,
                status=choices.BinStatus.EN_RECIBO,
                observation=observation,
                updated_at=timezone.now(),
            )
            report["still_unmapped"] += 1
            if old_position_id:
                report["cleared"] += 1

    # Liberar Andén (C1) que quedó ocupado por el import erróneo,
    # si ya no tiene bins activos/almacenados.
    if old_anden_ids:
        still_used = set(
            Bin.objects.filter(
                position_id__in=old_anden_ids,
                status__in=[
                    choices.BinStatus.ALMACENADO,
                    choices.BinStatus.EN_DESPACHO,
                ],
            ).values_list("position_id", flat=True)
        )
        free_ids = old_anden_ids - still_used
        if free_ids:
            report["anden_freed"] = Position.objects.filter(id__in=free_ids).update(
                status=choices.PositionStatus.LIBRE,
                updated_at=timezone.now(),
            )

    if new_occupied_ids:
        Position.objects.filter(id__in=new_occupied_ids).update(
            status=choices.PositionStatus.OCUPADO,
            updated_at=timezone.now(),
        )

    return report
