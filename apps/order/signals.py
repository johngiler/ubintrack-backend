from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Exit, ExitMovement, ExitMovementType


@receiver(pre_save, sender=Exit)
def track_exit_status_change(sender, instance, **kwargs):
    """
    Guarda el estado anterior antes de guardar para detectar cambios.
    """
    if instance.pk:
        try:
            old_instance = Exit.objects.get(pk=instance.pk)
            instance._old_status = old_instance.status
        except Exit.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None


@receiver(post_save, sender=Exit)
def create_exit_movement_on_save(sender, instance, created, **kwargs):
    """
    Registra movimientos cuando se crea o actualiza una orden de salida.
    """
    if created:
        ExitMovement.log_movement(
            exit_obj=instance,
            movement_type=ExitMovementType.CREATED,
            new_status=instance.status,
            notes=f'Orden de salida {instance.code} creada'
        )
    else:
        old_status = getattr(instance, '_old_status', None)
        if old_status and old_status != instance.status:
            ExitMovement.log_movement(
                exit_obj=instance,
                movement_type=ExitMovementType.STATUS_CHANGED,
                old_status=old_status,
                new_status=instance.status,
                notes=f'Estado cambiado de {old_status} a {instance.status}'
            )
