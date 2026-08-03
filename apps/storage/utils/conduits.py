"""Helpers de conducto: cava + pasillo + rack + nivel (places A–E)."""

from __future__ import annotations

from typing import Iterable

from django.core.exceptions import ValidationError
from django.db.models import QuerySet

from apps.core import choices
from apps.storage.models import Position

# Orden de llenado push: fondo → frente
PUSH_FILL_ORDER = ("E", "D", "C", "B", "A")
MIN_PUSH_DEPTH = 5


def conduit_filter_kwargs(position: Position) -> dict:
    return {
        "cava_id": position.cava_id,
        "hall": position.hall,
        "rack": position.rack,
        "level": position.level,
    }


def conduit_positions(position: Position) -> QuerySet[Position]:
    """Todas las posiciones del mismo conducto."""
    return Position.objects.filter(**conduit_filter_kwargs(position))


def conduit_depth(position: Position) -> int:
    """Cantidad de profundidades (places distintos) del conducto."""
    return (
        conduit_positions(position)
        .values_list("place", flat=True)
        .distinct()
        .count()
    )


def validate_conduit_push_depth(position: Position) -> None:
    """Push solo si el conducto tiene profundidad ≥ 5."""
    if conduit_depth(position) < MIN_PUSH_DEPTH:
        raise ValidationError(
            "La profundidad del conducto debe ser de al menos 5."
        )


def set_conduit_is_push(position: Position, is_push: bool) -> int:
    """Marca is_push en todo el conducto. Devuelve filas actualizadas."""
    if is_push:
        validate_conduit_push_depth(position)
    return conduit_positions(position).update(is_push=is_push)


def sort_places_for_push(places: Iterable[str]) -> list[str]:
    order = {p: i for i, p in enumerate(PUSH_FILL_ORDER)}
    return sorted(places, key=lambda p: order.get(p, 99))


def free_places_in_push_order(positions: Iterable[Position]) -> list[Position]:
    """Posiciones libres del conducto, ordenadas E→A."""
    libres = [
        p
        for p in positions
        if p.status == choices.PositionStatus.LIBRE and p.is_active
    ]
    order = {place: i for i, place in enumerate(PUSH_FILL_ORDER)}
    return sorted(libres, key=lambda p: order.get(p.place, 99))
