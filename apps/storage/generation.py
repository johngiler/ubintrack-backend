"""Crea las posiciones de una cava a partir de sus bloques."""

from apps.core.choices import PositionStatus
from apps.storage.models import Cava, CavaBlock, Position


def _aisle_code(blocks, index) -> str:
    """Pasillo de un rack: el de abajo si existe; si no, el de arriba."""
    for block in blocks[index + 1:]:
        if block.kind == CavaBlock.Kind.RACK:
            break
        if block.kind == CavaBlock.Kind.AISLE:
            code = (block.code or "").strip()
            if code:
                return code
    for block in reversed(blocks[:index]):
        if block.kind == CavaBlock.Kind.RACK:
            break
        if block.kind == CavaBlock.Kind.AISLE:
            code = (block.code or "").strip()
            if code:
                return code
    return ""


def iter_slots(cava: Cava):
    """Cada celda de rack: pasillo, rack, nivel y profundidad."""
    blocks = list(cava.blocks.filter(is_active=True).order_by("sort_order", "id"))
    for index, block in enumerate(blocks):
        if block.kind != CavaBlock.Kind.RACK:
            continue
        levels = block.levels or 0
        if levels < 1:
            continue
        aisle_code = (block.aisle_code or "").strip() or _aisle_code(blocks, index)
        for rack in block.rack_tokens():
            for level in range(1, levels + 1):
                for place in block.resolved_depth_codes():
                    yield aisle_code, rack, level, place


def generate_positions_for_cava(cava: Cava) -> dict:
    workspace_id = cava.storage.workspace_id
    created = 0
    skipped = 0
    expected = 0
    for hall, rack, level, place in iter_slots(cava):
        expected += 1
        exists = Position.objects.filter(
            cava=cava,
            hall=hall,
            rack=rack,
            level=level,
            place=place,
        ).exists()
        if exists:
            skipped += 1
            continue
        Position.objects.create(
            cava=cava,
            workspace_id=workspace_id,
            hall=hall,
            rack=rack,
            level=level,
            place=place,
            status=PositionStatus.LIBRE,
        )
        created += 1

    if expected:
        cava.positions_has_been_generated = True
        cava.save(update_fields=["positions_has_been_generated", "updated_at"])
    return {"expected": expected, "created": created, "skipped": skipped}
