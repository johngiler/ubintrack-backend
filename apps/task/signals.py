from django.db.models.signals import m2m_changed, post_save, pre_save
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
        models.TaskLog.objects.create(
            task=instance,
            message=f"Tarea creada ({instance.get_task_type_display()}) con estado '{instance.get_status_display()}'",
        )
        return

    old = getattr(instance, "_old_instance", None)
    if not old:
        return

    changes = []
    if old.status != instance.status:
        changes.append(
            f"Estado: '{old.get_status_display()}' → '{instance.get_status_display()}'"
        )
    if old.priority != instance.priority:
        changes.append(
            f"Prioridad: '{old.get_priority_display()}' → '{instance.get_priority_display()}'"
        )
    if old.title != instance.title:
        changes.append("Título actualizado")
    if old.started_at != instance.started_at:
        changes.append("Inicio actualizado")
    if old.completed_at != instance.completed_at:
        changes.append("Finalización actualizada")
    if old.observations != instance.observations:
        changes.append("Observaciones actualizadas")
    if old.client_id != instance.client_id:
        changes.append("Cliente actualizado")
    if list(old.halls or []) != list(instance.halls or []):
        changes.append("Pasillos actualizados")

    if changes:
        models.TaskLog.objects.create(task=instance, message="; ".join(changes))


@receiver(m2m_changed, sender=models.Task.assigned_to.through)
def task_assigned_to_changed(sender, instance, action, pk_set, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear"):
        return

    if action == "post_clear":
        message = "Asignación de usuarios limpiada"
    elif action == "post_add":
        users = CustomUser.objects.filter(pk__in=pk_set).values_list(
            "username", flat=True
        )
        message = f"Usuarios asignados: {', '.join(users) or '—'}"
    else:
        users = CustomUser.objects.filter(pk__in=pk_set).values_list(
            "username", flat=True
        )
        message = f"Usuarios desasignados: {', '.join(users) or '—'}"

    models.TaskLog.objects.create(task=instance, message=message)


@receiver(m2m_changed, sender=models.Task.cavas.through)
def task_cavas_changed(sender, instance, action, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    models.TaskLog.objects.create(task=instance, message="Cámaras actualizadas")


@receiver(m2m_changed, sender=models.Task.products.through)
def task_products_changed(sender, instance, action, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    models.TaskLog.objects.create(task=instance, message="Productos actualizados")
