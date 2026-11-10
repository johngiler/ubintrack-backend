"""Reglas de consistencia: is_active / is_picking / is_push."""

from __future__ import annotations

from django.core.exceptions import ValidationError


def validate_position_flags(*, is_active: bool, is_picking: bool, is_push: bool) -> None:
    """
    - Inactiva → no push ni picking.
    - Push y picking son mutuamente excluyentes.
    """
    if not is_active:
        if is_push:
            raise ValidationError(
                {"is_push": "No se puede activar push en una posición inactiva."}
            )
        if is_picking:
            raise ValidationError(
                {"is_picking": "No se puede activar picking en una posición inactiva."}
            )
        return
    if is_push and is_picking:
        raise ValidationError(
            {
                "is_picking": "Push y picking no pueden estar activos a la vez.",
                "is_push": "Push y picking no pueden estar activos a la vez.",
            }
        )
