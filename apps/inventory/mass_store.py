"""
Almacenamiento masivo: criterios de almacenamiento → validar → sugerir → aplicar.

Criterios:
- push: conductos is_push, llenado E→A, homogeneidad product_id+batch.
- compact_sku_batch: prioriza posiciones cerca de bins ya almacenados del mismo SKU+lote.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.core import choices
from apps.inventory.models import Bin, BinMovement, BinMovementType, BinProduct
from apps.storage.models import Position
from apps.storage.utils.conduits import free_places_in_push_order
from apps.storage.utils.push_validation import (
    ProductBatchKey,
    bin_product_batch_key,
    conduit_occupied_product_batch,
    validate_push_assignment,
)


@dataclass
class Assignment:
    bin_id: int
    position_id: int
    bin_code: str
    position_code: str
    conduit_key: str


class MassStoreCriterion(Protocol):
    id: str
    label: str
    description: str

    def validate(self, bins: list[Bin], workspace_id: int, params: dict) -> None: ...

    def suggest(
        self, bins: list[Bin], workspace_id: int, params: dict
    ) -> list[Assignment]: ...


def _conduit_key(cava_id, hall, rack, level) -> str:
    return f"{cava_id}|{hall}|{rack}|{level}"


def _validate_bins_common(bins: list[Bin], workspace_id: int) -> None:
    if not bins:
        raise ValidationError("Seleccione al menos un bin.")

    for bin_obj in bins:
        if bin_obj.workspace_id != workspace_id:
            raise ValidationError("Hay bins fuera del workspace.")
        if not bin_obj.is_active:
            raise ValidationError(f"El bin {bin_obj.code} no está activo.")
        if bin_obj.status not in (
            choices.BinStatus.EN_RECIBO,
            choices.BinStatus.ALMACENADO,
        ):
            raise ValidationError(
                f"El bin {bin_obj.code} no puede almacenarse en este estado."
            )
        if bin_product_batch_key(bin_obj) is None:
            raise ValidationError(
                f"El bin {bin_obj.code} no tiene producto/lote."
            )
        if bin_obj.position_id:
            raise ValidationError(
                f"El bin {bin_obj.code} ya tiene posición."
            )


def _halls_with_product(workspace_id: int, key: ProductBatchKey) -> set[tuple]:
    """(cava_id, hall) donde ya hay bins almacenados del mismo product+batch."""
    qs = Bin.objects.filter(
        workspace_id=workspace_id,
        is_active=True,
        status=choices.BinStatus.ALMACENADO,
        position__isnull=False,
        bin_products__entry_product__product_id=key.product_id,
    )
    if key.batch:
        qs = qs.filter(bin_products__entry_product__batch=key.batch)
    else:
        qs = qs.filter(
            Q(bin_products__entry_product__batch__isnull=True)
            | Q(bin_products__entry_product__batch="")
        )
    rows = qs.values_list("position__cava_id", "position__hall").distinct()
    return {(cava_id, hall) for cava_id, hall in rows if cava_id and hall}


def _slots_with_product(
    workspace_id: int, key: ProductBatchKey
) -> set[tuple[int, str, int, int]]:
    """(cava_id, hall, rack, level) con bins almacenados del mismo product+batch."""
    qs = Bin.objects.filter(
        workspace_id=workspace_id,
        is_active=True,
        status=choices.BinStatus.ALMACENADO,
        position__isnull=False,
        bin_products__entry_product__product_id=key.product_id,
    )
    if key.batch:
        qs = qs.filter(bin_products__entry_product__batch=key.batch)
    else:
        qs = qs.filter(
            Q(bin_products__entry_product__batch__isnull=True)
            | Q(bin_products__entry_product__batch="")
        )
    rows = qs.values_list(
        "position__cava_id",
        "position__hall",
        "position__rack",
        "position__level",
    ).distinct()
    return {
        (cava_id, hall, rack, level)
        for cava_id, hall, rack, level in rows
        if cava_id and hall and rack is not None and level is not None
    }


def _position_ok_for_key(position: Position, key: ProductBatchKey) -> bool:
    """Si es push, no mezclar otro product+batch en el conducto."""
    if not position.is_push:
        return True
    existing = conduit_occupied_product_batch(position)
    return existing is None or existing == key


def _proximity_score_to_slots(
    position: Position,
    preferred: set[tuple[int, str, int, int]],
) -> int:
    best = 0
    for cava_id, hall, rack, level in preferred:
        if (
            position.cava_id == cava_id
            and position.hall == hall
            and position.rack == rack
            and position.level == level
        ):
            best = max(best, 4)
        elif (
            position.cava_id == cava_id
            and position.hall == hall
            and position.rack == rack
        ):
            best = max(best, 3)
        elif position.cava_id == cava_id and position.hall == hall:
            best = max(best, 2)
        elif position.cava_id == cava_id:
            best = max(best, 1)
    return best


def _assignment(bin_obj: Bin, position: Position) -> Assignment:
    return Assignment(
        bin_id=bin_obj.id,
        position_id=position.id,
        bin_code=bin_obj.code,
        position_code=position.code,
        conduit_key=_conduit_key(
            position.cava_id, position.hall, position.rack, position.level
        ),
    )


class PushConduitCriterion:
    id = "push"
    label = "Push (conducto)"
    description = "Conductos push, llenado de fondo a frente."

    def validate(self, bins: list[Bin], workspace_id: int, params: dict) -> None:
        _validate_bins_common(bins, workspace_id)

    def suggest(
        self, bins: list[Bin], workspace_id: int, params: dict
    ) -> list[Assignment]:
        self.validate(bins, workspace_id, params)

        by_key: dict[ProductBatchKey, list[Bin]] = defaultdict(list)
        for bin_obj in bins:
            by_key[bin_product_batch_key(bin_obj)].append(bin_obj)

        assignments: list[Assignment] = []
        reserved_position_ids: set[int] = set()

        for key, group in by_key.items():
            assignments.extend(
                self._suggest_for_group(
                    group, key, workspace_id, reserved_position_ids
                )
            )
        return assignments

    def _suggest_for_group(
        self,
        bins: list[Bin],
        key: ProductBatchKey,
        workspace_id: int,
        reserved_position_ids: set[int],
    ) -> list[Assignment]:
        preferred_halls = _halls_with_product(workspace_id, key)

        push_positions = list(
            Position.objects.filter(
                workspace_id=workspace_id,
                is_push=True,
                is_active=True,
                cava__isnull=False,
            )
            .select_related("cava")
            .order_by("cava_id", "hall", "rack", "level", "place")
        )

        conduits: dict[str, list[Position]] = defaultdict(list)
        for pos in push_positions:
            ck = _conduit_key(pos.cava_id, pos.hall, pos.rack, pos.level)
            conduits[ck].append(pos)

        partial: list[tuple[str, list[Position], int]] = []
        empty: list[tuple[str, list[Position], int]] = []

        for ck, positions in conduits.items():
            free = [
                p
                for p in free_places_in_push_order(positions)
                if p.id not in reserved_position_ids
            ]
            if not free:
                continue

            occupied_elsewhere = any(
                p.status != choices.PositionStatus.LIBRE for p in positions
            )
            if occupied_elsewhere:
                existing = conduit_occupied_product_batch(positions[0])
                if existing is not None and existing != key:
                    continue
                score = self._proximity_score(positions[0], preferred_halls)
                partial.append((ck, free, score))
            else:
                score = self._proximity_score(positions[0], preferred_halls)
                empty.append((ck, free, score))

        partial.sort(key=lambda x: (-x[2], x[0]))
        empty.sort(key=lambda x: (-x[2], -len(x[1]), x[0]))

        free_slots: list[Position] = []
        for _, free, _ in partial:
            free_slots.extend(free)
        for _, free, _ in empty:
            free_slots.extend(free)

        if len(free_slots) < len(bins):
            raise ValidationError(
                f"No hay suficientes posiciones push libres "
                f"({len(free_slots)} disponibles, {len(bins)} bins)."
            )

        result: list[Assignment] = []
        for bin_obj, position in zip(bins, free_slots):
            reserved_position_ids.add(position.id)
            result.append(_assignment(bin_obj, position))
        return result

    @staticmethod
    def _proximity_score(position: Position, preferred_halls: set[tuple]) -> int:
        if (position.cava_id, position.hall) in preferred_halls:
            return 2
        if any(c == position.cava_id for c, _ in preferred_halls):
            return 1
        return 0


class CompactSkuBatchCriterion:
    id = "compact_sku_batch"
    label = "Compactar SKU y lote"
    description = "Prioriza posiciones cerca del mismo producto y lote."

    def validate(self, bins: list[Bin], workspace_id: int, params: dict) -> None:
        _validate_bins_common(bins, workspace_id)

    def suggest(
        self, bins: list[Bin], workspace_id: int, params: dict
    ) -> list[Assignment]:
        self.validate(bins, workspace_id, params)

        by_key: dict[ProductBatchKey, list[Bin]] = defaultdict(list)
        for bin_obj in bins:
            by_key[bin_product_batch_key(bin_obj)].append(bin_obj)

        assignments: list[Assignment] = []
        reserved_position_ids: set[int] = set()

        for key, group in by_key.items():
            assignments.extend(
                self._suggest_for_group(
                    group, key, workspace_id, reserved_position_ids
                )
            )
        return assignments

    def _suggest_for_group(
        self,
        bins: list[Bin],
        key: ProductBatchKey,
        workspace_id: int,
        reserved_position_ids: set[int],
    ) -> list[Assignment]:
        preferred = _slots_with_product(workspace_id, key)

        free_positions = list(
            Position.objects.filter(
                workspace_id=workspace_id,
                is_active=True,
                status=choices.PositionStatus.LIBRE,
                cava__isnull=False,
            )
            .exclude(id__in=reserved_position_ids)
            .select_related("cava")
            .order_by("cava_id", "hall", "rack", "level", "place")
        )

        ranked: list[tuple[int, int, Position]] = []
        push_place_order = {"E": 0, "D": 1, "C": 2, "B": 3, "A": 4}
        for pos in free_positions:
            if not _position_ok_for_key(pos, key):
                continue
            score = _proximity_score_to_slots(pos, preferred)
            if pos.is_push:
                place_rank = push_place_order.get(pos.place, 99)
            else:
                place_rank = ord(pos.place or "Z")
            ranked.append((score, place_rank, pos))

        ranked.sort(key=lambda x: (-x[0], x[1], x[2].code or ""))
        free_slots = [pos for _, _, pos in ranked]

        if len(free_slots) < len(bins):
            raise ValidationError(
                f"No hay suficientes posiciones libres "
                f"({len(free_slots)} disponibles, {len(bins)} bins)."
            )

        result: list[Assignment] = []
        for bin_obj, position in zip(bins, free_slots):
            reserved_position_ids.add(position.id)
            result.append(_assignment(bin_obj, position))
        return result


CRITERIA: dict[str, MassStoreCriterion] = {
    PushConduitCriterion.id: PushConduitCriterion(),
    CompactSkuBatchCriterion.id: CompactSkuBatchCriterion(),
}


def list_storage_criteria() -> list[dict]:
    return [
        {
            "id": c.id,
            "label": c.label,
            "description": c.description,
        }
        for c in CRITERIA.values()
    ]


def get_criterion(strategy: str) -> MassStoreCriterion:
    criterion = CRITERIA.get(strategy)
    if criterion is None:
        raise ValidationError("Criterio de almacenamiento no soportado.")
    return criterion


def load_bins_for_mass_store(bin_ids: list[int], workspace_id: int) -> list[Bin]:
    qs = (
        Bin.objects.filter(id__in=bin_ids, workspace_id=workspace_id)
        .select_related("position", "client", "entry")
        .prefetch_related(
            Prefetch(
                "bin_products",
                queryset=BinProduct.objects.select_related(
                    "entry_product__product"
                ).filter(is_active=True),
            )
        )
    )
    by_id = {b.id: b for b in qs}
    missing = [i for i in bin_ids if i not in by_id]
    if missing:
        raise ValidationError("Uno o más bins no existen o están fuera del workspace.")
    return [by_id[i] for i in bin_ids]


def suggest_mass_store(
    *,
    bin_ids: list[int],
    workspace_id: int,
    strategy: str = "push",
    params: dict | None = None,
) -> list[Assignment]:
    bins = load_bins_for_mass_store(bin_ids, workspace_id)
    criterion = get_criterion(strategy)
    return criterion.suggest(bins, workspace_id, params or {})


@transaction.atomic
def apply_mass_store(
    *,
    bin_ids: list[int],
    workspace_id: int,
    strategy: str = "push",
    params: dict | None = None,
    user=None,
    assignments: list[dict] | None = None,
) -> dict:
    """
    Sugiere (o usa assignments del preview) y aplica:
    position_id + bin almacenado + posición ocupada + movimiento.
    """
    bins = load_bins_for_mass_store(bin_ids, workspace_id)
    criterion = get_criterion(strategy)

    if assignments:
        planned = [
            Assignment(
                bin_id=a["bin_id"],
                position_id=a["position_id"],
                bin_code=a.get("bin_code", ""),
                position_code=a.get("position_code", ""),
                conduit_key=a.get("conduit_key", ""),
            )
            for a in assignments
        ]
        pos_map = {
            p.id: p
            for p in Position.objects.filter(
                id__in=[a.position_id for a in planned],
                workspace_id=workspace_id,
            )
        }
        bin_map = {b.id: b for b in bins}
        for a in planned:
            bin_obj = bin_map.get(a.bin_id)
            position = pos_map.get(a.position_id)
            if not bin_obj or not position:
                raise ValidationError("Asignación inválida.")
            if position.status != choices.PositionStatus.LIBRE:
                raise ValidationError(
                    f"La posición {position.code} ya no está libre."
                )
            validate_push_assignment(position, bin_obj, exclude_bin_id=bin_obj.pk)
    else:
        planned = criterion.suggest(bins, workspace_id, params or {})

    bin_map = {b.id: b for b in bins}
    now = timezone.now()
    updated = 0
    movements = []

    for a in planned:
        bin_obj = bin_map[a.bin_id]
        position = Position.objects.select_for_update().get(
            pk=a.position_id, workspace_id=workspace_id
        )
        if position.status != choices.PositionStatus.LIBRE:
            raise ValidationError(f"La posición {position.code} ya no está libre.")

        validate_push_assignment(position, bin_obj, exclude_bin_id=bin_obj.pk)

        old_status = bin_obj.status
        old_position = bin_obj.position

        if old_position_id := (old_position.pk if old_position else None):
            if old_position_id != position.pk:
                Position.objects.filter(pk=old_position_id).update(
                    status=choices.PositionStatus.LIBRE,
                    updated_at=now,
                )

        update_fields = {
            "position_id": position.pk,
            "status": choices.BinStatus.ALMACENADO,
            "updated_at": now,
        }
        if user is not None:
            update_fields["updated_by"] = user
        Bin.objects.filter(pk=bin_obj.pk).update(**update_fields)
        Position.objects.filter(pk=position.pk).update(
            status=choices.PositionStatus.OCUPADO,
            updated_at=now,
        )

        initial_qty = sum(
            (bp.quantity or 0) for bp in bin_obj.bin_products.all()
        )
        movements.append(
            BinMovement(
                bin_id=bin_obj.pk,
                movement_type=BinMovementType.STATUS_CHANGED,
                old_status=old_status,
                new_status=choices.BinStatus.ALMACENADO,
                position_id=position.pk,
                initial_quantity=initial_qty,
                notes=(
                    f"Almacenamiento masivo (criterio {strategy})"
                    + (
                        f" por {user.get_full_name() or user.username}"
                        if user
                        else ""
                    )
                    + "."
                ),
                created_by=user,
                updated_by=user,
            )
        )
        updated += 1

    if movements:
        BinMovement.objects.bulk_create(movements)

    return {
        "success": True,
        "updated": updated,
        "strategy": strategy,
        "assignments": [
            {
                "bin_id": a.bin_id,
                "position_id": a.position_id,
                "bin_code": a.bin_code,
                "position_code": a.position_code,
                "conduit_key": a.conduit_key,
            }
            for a in planned
        ],
        "message": f"{updated} bins almacenados.",
    }
