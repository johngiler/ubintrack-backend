from django.db.models.signals import m2m_changed, post_save, pre_save
from django.dispatch import receiver

from apps.core.models import CustomUser
from apps.core.utils.display_name import user_display_name
from apps.task.services.task_log import (
    task_log_signals_are_muted,
    write_task_log,
)

from . import models


@receiver(pre_save, sender=models.Task)
def pre_save_task(sender, instance, **kwargs):
    if instance.pk:
        try:
            instance._old_instance = models.Task.objects.get(pk=instance.pk)
        except models.Task.DoesNotExist:
            instance._old_instance = None


@receiver(post_save, sender=models.Task)
def post_save_task(sender, instance, created, **kwargs):
    if task_log_signals_are_muted():
        return

    if created:
        write_task_log(
            task=instance,
            message=(
                f"Tarea creada ({instance.get_task_type_display()}) "
                f"con estado {instance.get_status_display()}"
            ),
            kind=models.TaskLog.Kind.WORKFLOW,
        )
        return

    old = getattr(instance, "_old_instance", None)
    if not old:
        return

    workflow = []
    scope = []
    meta = []

    if old.status != instance.status:
        workflow.append(
            f"Estado: {old.get_status_display()} → {instance.get_status_display()}"
        )
    if old.client_id != instance.client_id:
        scope.append("Cliente actualizado")
    if list(old.halls or []) != list(instance.halls or []):
        scope.append("Pasillos actualizados")
    if old.priority != instance.priority:
        meta.append(
            f"Prioridad: {old.get_priority_display()} → {instance.get_priority_display()}"
        )
    if old.title != instance.title:
        meta.append("Título actualizado")
    if old.started_at != instance.started_at:
        meta.append("Inicio actualizado")
    if old.completed_at != instance.completed_at:
        meta.append("Finalización actualizada")
    if old.observations != instance.observations:
        meta.append("Observaciones actualizadas")
    if old.require_approval != instance.require_approval:
        meta.append("Aprobación actualizada")

    if workflow:
        write_task_log(
            task=instance,
            message="; ".join(workflow),
            kind=models.TaskLog.Kind.WORKFLOW,
        )
    if scope:
        write_task_log(
            task=instance,
            message="; ".join(scope),
            kind=models.TaskLog.Kind.SCOPE,
        )
    if meta:
        write_task_log(
            task=instance,
            message="; ".join(meta),
            kind=models.TaskLog.Kind.META,
        )


def _user_labels(pk_set):
    users = CustomUser.objects.filter(pk__in=pk_set)
    return ", ".join(user_display_name(u) for u in users) or "—"


@receiver(m2m_changed, sender=models.Task.assigned_to.through)
def task_assigned_to_changed(sender, instance, action, pk_set, **kwargs):
    if task_log_signals_are_muted():
        return
    if action not in ("post_add", "post_remove", "post_clear"):
        return

    if action == "post_clear":
        message = "Asignación de usuarios limpiada"
    elif action == "post_add":
        message = f"Usuarios asignados: {_user_labels(pk_set)}"
    else:
        message = f"Usuarios desasignados: {_user_labels(pk_set)}"

    write_task_log(
        task=instance,
        message=message,
        kind=models.TaskLog.Kind.WORKFLOW,
    )


@receiver(m2m_changed, sender=models.Task.cavas.through)
def task_cavas_changed(sender, instance, action, **kwargs):
    if task_log_signals_are_muted():
        return
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    write_task_log(
        task=instance,
        message="Cámaras actualizadas",
        kind=models.TaskLog.Kind.SCOPE,
    )


@receiver(m2m_changed, sender=models.Task.products.through)
def task_products_changed(sender, instance, action, **kwargs):
    if task_log_signals_are_muted():
        return
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    write_task_log(
        task=instance,
        message="Productos actualizados",
        kind=models.TaskLog.Kind.SCOPE,
    )
