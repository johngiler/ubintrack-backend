"""Acciones de recorrido: verificar posición, contar, no existe, reopen."""

from __future__ import annotations

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.choices import TaskLineStatus
from apps.task import models as task_models
from apps.task.services.build_lines import bin_system_quantity
from apps.task.services.task_log import write_task_log


def resolve_position_code(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    # Si el QR trae URL u otro ruido, quedarse con el último token no vacío.
    if "/" in text:
        text = text.rstrip("/").split("/")[-1]
    return text.strip()


def verify_position(*, task, position_code: str, actor=None, line_id=None) -> list:
    code = resolve_position_code(position_code)
    if not code:
        raise ValidationError({"detail": "No se pudo completar la operación."})

    qs = task.lines.select_related("position", "bin")
    if line_id is not None:
        line = qs.filter(pk=line_id).first()
        if line is None:
            raise ValidationError({"detail": "No se pudo completar la operación."})
        expected = str(getattr(line.position, "code", "") or "").strip()
        if code.casefold() != expected.casefold():
            raise ValidationError({"detail": "No se pudo completar la operación."})
        lines = [line]
    else:
        lines = list(qs.filter(position__code__iexact=code))
        if not lines:
            raise ValidationError({"detail": "No se pudo completar la operación."})

    now = timezone.now()
    updated = []
    for line in lines:
        if line.verified_at is None:
            line.verified_at = now
            line.save(update_fields=["verified_at", "updated_at"])
            updated.append(line)

    if updated:
        write_task_log(
            task=task,
            message=f"Posición {code} verificada ({len(updated)} línea(s))",
            kind=task_models.TaskLog.Kind.WORKFLOW,
            actor=actor,
        )
    return lines


def apply_count(*, line, counted_quantity: int, actor=None) -> task_models.TaskLine:
    if line.verified_at is None:
        raise ValidationError({"detail": "Verifique la posición antes de contar."})
    if counted_quantity is None or int(counted_quantity) < 0:
        raise ValidationError({"detail": "No se pudo completar la operación."})

    qty = int(counted_quantity)
    system_qty = bin_system_quantity(line.bin)
    line.counted_quantity = qty
    line.system_quantity = system_qty
    line.line_status = (
        TaskLineStatus.COUNTED if qty == system_qty else TaskLineStatus.DISCREPANCY
    )
    line.verified_at = line.verified_at or timezone.now()
    line.save(
        update_fields=[
            "counted_quantity",
            "system_quantity",
            "line_status",
            "verified_at",
            "updated_at",
        ]
    )

    if line.bin_id:
        bin_obj = line.bin
        bin_obj.last_audit = timezone.now()
        bin_obj.last_audit_quantity = qty
        bin_obj.save(update_fields=["last_audit", "last_audit_quantity", "updated_at"])

    write_task_log(
        task=line.task,
        message=(
            f"{line.position.code}: {line.get_line_status_display()} "
            f"(sistema {system_qty} / contado {qty})"
        ),
        kind=task_models.TaskLog.Kind.WORKFLOW,
        actor=actor,
    )
    return line


def mark_missing_bin(*, line, actor=None) -> task_models.TaskLine:
    line.line_status = TaskLineStatus.MISSING_BIN
    line.counted_quantity = None
    line.verified_at = line.verified_at or timezone.now()
    line.save(
        update_fields=["line_status", "counted_quantity", "verified_at", "updated_at"]
    )
    write_task_log(
        task=line.task,
        message=f"{line.position.code}: no existe bin",
        kind=task_models.TaskLog.Kind.WORKFLOW,
        actor=actor,
    )
    return line


def reopen_line(*, line, actor=None) -> task_models.TaskLine:
    line.line_status = TaskLineStatus.PENDING
    line.counted_quantity = None
    line.system_quantity = None
    line.verified_at = None
    line.save(
        update_fields=[
            "line_status",
            "counted_quantity",
            "system_quantity",
            "verified_at",
            "updated_at",
        ]
    )
    write_task_log(
        task=line.task,
        message=f"{line.position.code}: línea reabierta",
        kind=task_models.TaskLog.Kind.WORKFLOW,
        actor=actor,
    )
    return line


def task_has_pending_lines(task) -> bool:
    return task.lines.filter(line_status=TaskLineStatus.PENDING).exists()


def next_status_after_lines(task) -> str:
    from apps.core.choices import TaskStatus

    if task.require_approval:
        return TaskStatus.VERIFICATION
    return TaskStatus.CLOSED
