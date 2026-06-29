from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

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
    # If the task was just created, log its creation.
    if created:
        message = f"Tarea creada con estado '{instance.status}'"
        models.TaskLog.objects.create(task=instance, message=message)
    else:
        old = getattr(instance, '_old_instance', None)
        changes = []
        if old:
            if old.status != instance.status:
                changes.append(
                    f"Estado cambiado de '{old.status}' a '{instance.status}'"
                )
            if old.assigned_to != instance.assigned_to:
                changes.append(
                    f"Asignado cambiado de '{old.assigned_to}' a '{
                        instance.assigned_to}'"
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
                    f"Finalización cambiada de '{
                        old.completed_at}' a '{instance.completed_at}'"
                )
            if old.observations != instance.observations:
                changes.append("Observaciones actualizadas")

        if changes:
            message = "; ".join(changes)
            models.TaskLog.objects.create(task=instance, message=message)
