"""Crea las posiciones de una cava a partir de sus bloques."""

from apps.core.choices import PositionStatus
from apps.storage.models import Cava, CavaBlock, Position
from apps.storage.utils.floor_neighbors import (
    floor_hall_token,
    floor_is_active,
    section_neighbors,
)

# Sin sección explícita (no heredar vecino). Coincide con AISLE_NONE del frontend.
AISLE_NONE = "-"


def _neighbor_section_code(blocks, index) -> str:
    """Sección contigua: la de abajo si existe; si no, la de arriba."""
    for block in blocks[index + 1 :]:
        if block.kind == CavaBlock.Kind.RACK:
            break
        if block.kind == CavaBlock.Kind.SECTION:
            code = (block.code or "").strip()
            if code:
                return code
        if block.kind == CavaBlock.Kind.FLOOR:
            continue
    for block in reversed(blocks[:index]):
        if block.kind == CavaBlock.Kind.RACK:
            break
        if block.kind == CavaBlock.Kind.SECTION:
            code = (block.code or "").strip()
            if code:
                return code
        if block.kind == CavaBlock.Kind.FLOOR:
            continue
    return ""


def resolve_aisle_code(block, blocks, index) -> str:
    """Explícito, NONE→vacío, o herencia por cercanía si aisle_code vacío."""
    chosen = (block.aisle_code or "").strip()
    if chosen == AISLE_NONE:
        return ""
    if chosen:
        return chosen
    return _neighbor_section_code(blocks, index)


def iter_floor_slots(block: CavaBlock, blocks, index: int):
    """Posiciones de piso: hall compuesto de secciones vecinas + place 01..N."""
    neighbors = section_neighbors(blocks, index)
    if not floor_is_active(neighbors):
        return
    hall = floor_hall_token(neighbors)
    count = int(block.rack_count or 0)
    if not hall or count < 1:
        return
    for place_index in range(1, count + 1):
        yield hall, "", 1, f"{place_index:02d}"


def iter_slots(cava: Cava):
    """Celdas de rack y slots de piso."""
    blocks = list(cava.blocks.filter(is_active=True).order_by("sort_order", "id"))
    for index, block in enumerate(blocks):
        if block.kind == CavaBlock.Kind.FLOOR:
            yield from iter_floor_slots(block, blocks, index)
            continue
        if block.kind == CavaBlock.Kind.SECTION:
            continue
        if block.kind != CavaBlock.Kind.RACK:
            continue
        levels = block.levels or 0
        if levels < 1:
            continue
        aisle_code = resolve_aisle_code(block, blocks, index)
        for rack in block.rack_tokens():
            for level in range(1, levels + 1):
                for place in block.resolved_depth_codes():
                    yield aisle_code, rack, level, place


def generate_positions_for_cava(cava: Cava) -> dict:
    """Crea solo las posiciones que faltan; no toca las ya existentes."""
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
