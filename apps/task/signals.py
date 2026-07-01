from django.db.models.signals import pre_save, post_save, m2m_changed
from django.dispatch import receiver

from apps.core.models import CustomUser

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
    if created:
        message = f"Tarea creada con estado '{instance.status}'"
        models.TaskLog.objects.create(task=instance, message=message)
        return

    old = getattr(instance, '_old_instance', None)
    if not old:
        return

    changes = []
    if old.status != instance.status:
        changes.append(
            f"Estado cambiado de '{old.status}' a '{instance.status}'"
        )
    if old.priority != instance.priority:
        changes.append(
            f"Prioridad cambiada de '{old.priority}' a '{instance.priority}'"
        )
    if old.started_at != instance.started_at:
        changes.append(
            f"Inicio cambiado de '{old.started_at}' a '{instance.started_at}'"
        )
    if old.completed_at != instance.completed_at:
        changes.append(
            f"Finalización cambiada de '{old.completed_at}' a '{instance.completed_at}'"
        )
    if old.observations != instance.observations:
        changes.append('Observaciones actualizadas')
    if old.entry_id != instance.entry_id:
        changes.append('Orden de entrada actualizada')
    if old.exit_id != instance.exit_id:
        changes.append('Orden de salida actualizada')

    if changes:
        message = '; '.join(changes)
        models.TaskLog.objects.create(task=instance, message=message)


@receiver(m2m_changed, sender=models.Task.assigned_to.through)
def task_assigned_to_changed(sender, instance, action, pk_set, **kwargs):
    if action not in ('post_add', 'post_remove', 'post_clear'):
        return

    if action == 'post_clear':
        message = 'Asignación de usuarios limpiada'
    elif action == 'post_add':
        users = CustomUser.objects.filter(pk__in=pk_set).values_list(
            'username', flat=True)
        message = f"Usuarios asignados: {', '.join(users) or '—'}"
    else:
        users = CustomUser.objects.filter(pk__in=pk_set).values_list(
            'username', flat=True)
        message = f"Usuarios desasignados: {', '.join(users) or '—'}"

    models.TaskLog.objects.create(task=instance, message=message)
