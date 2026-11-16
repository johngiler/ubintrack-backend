"""Escritura de TaskLog + helpers de mensaje (historial de tarea)."""

from __future__ import annotations

from contextlib import contextmanager
from threading import local

from apps.core.utils.display_name import user_display_name
from apps.task import models as task_models

_tls = local()


@contextmanager
def task_log_signals_muted():
    """Silencia signals de TaskLog mientras el viewset escribe el historial."""
    prev = getattr(_tls, "muted", False)
    _tls.muted = True
    try:
        yield
    finally:
        _tls.muted = prev


def task_log_signals_are_muted() -> bool:
    return bool(getattr(_tls, "muted", False))


def actor_label(user) -> str:
    return user_display_name(user)


def assignees_label(users) -> str:
    names = []
    for user in users:
        label = user_display_name(user)
        if label and label not in names:
            names.append(label)
    return ", ".join(names) if names else "—"


def write_task_log(*, task, message: str, kind: str, actor=None):
    """Crea un TaskLog. CurrentUserField rellena created_by si hay request."""
    return task_models.TaskLog.objects.create(
        task=task,
        message=message,
        kind=kind,
    )


def log_task_created(*, task, actor=None):
    actor_name = actor_label(actor)
    assignees = list(task.assigned_to.all())
    status = task.get_status_display()
    type_label = task.get_task_type_display()
    parts = [f"{actor_name} creó la tarea ({type_label}) con estado {status}"]
    if assignees:
        parts.append(f"Asignados: {assignees_label(assignees)}")
    write_task_log(
        task=task,
        message=" · ".join(parts),
        kind=task_models.TaskLog.Kind.WORKFLOW,
        actor=actor,
    )


def log_task_status_change(*, task, old_status: str, actor=None):
    actor_name = actor_label(actor)
    old_label = dict(task_models.Task._meta.get_field("status").choices).get(
        old_status, old_status
    )
    new_label = task.get_status_display()
    parts = [f"{actor_name} pasó {old_label} → {new_label}"]
    assignees = list(task.assigned_to.all())
    if task.status == "asignada" and assignees:
        parts.append(f"Asignados: {assignees_label(assignees)}")
    if task.status == "verificacion" and actor:
        parts.append(f"Verificado por: {actor_name}")
    if task.status == "completada" and actor:
        parts.append(f"Completado por: {actor_name}")
    write_task_log(
        task=task,
        message=" · ".join(parts),
        kind=task_models.TaskLog.Kind.WORKFLOW,
        actor=actor,
    )


def log_task_reassign(*, task, added, removed, actor=None):
    actor_name = actor_label(actor)
    bits = []
    if added:
        bits.append(f"asignó a {assignees_label(added)}")
    if removed:
        bits.append(f"desasignó a {assignees_label(removed)}")
    if not bits:
        return
    write_task_log(
        task=task,
        message=f"{actor_name} {' y '.join(bits)}",
        kind=task_models.TaskLog.Kind.WORKFLOW,
        actor=actor,
    )


def log_task_scope_change(*, task, changes: list[str], actor=None):
    if not changes:
        return
    actor_name = actor_label(actor)
    write_task_log(
        task=task,
        message=f"{actor_name}: {'; '.join(changes)}",
        kind=task_models.TaskLog.Kind.SCOPE,
        actor=actor,
    )


def log_task_meta_change(*, task, changes: list[str], actor=None):
    if not changes:
        return
    actor_name = actor_label(actor)
    write_task_log(
        task=task,
        message=f"{actor_name}: {'; '.join(changes)}",
        kind=task_models.TaskLog.Kind.META,
        actor=actor,
    )


def describe_update_changes(*, old, new) -> tuple[list[str], list[str], bool]:
    """
    Compara campos escalares (no M2M).
    Returns (scope_changes, meta_changes, status_changed).
    """
    scope = []
    meta = []
    status_changed = old.status != new.status

    if old.client_id != new.client_id:
        scope.append("Cliente actualizado")
    if list(old.halls or []) != list(new.halls or []):
        scope.append("Pasillos actualizados")

    if old.priority != new.priority:
        meta.append(
            f"Prioridad {old.get_priority_display()} → {new.get_priority_display()}"
        )
    if old.title != new.title:
        meta.append("Título actualizado")
    if old.observations != new.observations:
        meta.append("Observaciones actualizadas")
    if old.require_approval != new.require_approval:
        meta.append("Aprobación actualizada")
    if old.started_at != new.started_at:
        meta.append("Inicio actualizado")
    if old.completed_at != new.completed_at:
        meta.append("Finalización actualizada")

    return scope, meta, status_changed
