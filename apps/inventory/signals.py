from django.db.models.signals import post_save, post_delete, pre_save
from django.db.models import Sum
from django.dispatch import receiver

from apps.core.choices import OrderStatus
from . import models


@receiver(post_save, sender=models.Bin)
def update_entry_status_on_bin_creation(sender, instance, created, **kwargs):
    bins = instance.entry.bins.all()
    if bins.count() == 1:
        instance.entry.status = OrderStatus.EN_PROGRESO
        instance.entry.save(update_fields=['status'])


@receiver(post_delete, sender=models.Bin)
def update_entry_status_on_bin_deletion(sender, instance, **kwargs):
    bins = instance.entry.bins.all()
    if bins.count() == 0:
        instance.entry.status = OrderStatus.PENDIENTE
        instance.entry.save(update_fields=['status'])


@receiver(post_save, sender=models.BinProduct)
def update_entry_product_inventory_status(sender, instance, **kwargs):
    """
    Actualiza el estado de inventario del producto de entrada cuando
    se modifica un producto en un bin.
    """
    if instance.entry_product:
        instance.entry_product.update_inventory_status()


@receiver(post_delete, sender=models.BinProduct)
def update_entry_product_inventory_status_on_delete(sender, instance, **kwargs):
    """
    Actualiza el estado de inventario del producto de entrada cuando
    se elimina un producto de un bin.
    """
    if instance.entry_product:
        instance.entry_product.update_inventory_status()


# Señales para tracking de movimientos de bins
@receiver(pre_save, sender=models.Bin)
def track_bin_status_change(sender, instance, **kwargs):
    """
    Guarda el estado anterior antes de guardar para poder compararlo después.
    """
    if instance.pk:
        try:
            old_instance = models.Bin.objects.get(pk=instance.pk)
            instance._old_status = old_instance.status
        except models.Bin.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None


@receiver(post_save, sender=models.Bin)
def create_bin_movement_on_save(sender, instance, created, **kwargs):
    """
    Crea registros de movimiento para:
    1. Creación de bin
    2. Cambios de estado
    """
    if created:
        # Registrar creación del bin
        models.BinMovement.objects.create(
            bin=instance,
            movement_type=models.BinMovementType.CREATED,
            new_status=instance.status,
            notes=f"Bin creado para la orden de entrada {instance.entry.code}"
        )
    else:
        # Verificar si cambió el estado
        old_status = getattr(instance, '_old_status', None)
        if old_status and old_status != instance.status:
            # Si cambia a ALMACENADO, incluir la posición y cantidad inicial
            position = None
            initial_quantity = None
            notes = f"Estado cambiado de {old_status} a {instance.status}"

            if instance.status == 'almacenado':
                # Calcular la cantidad total del bin
                initial_quantity = instance.bin_products.aggregate(
                    total=Sum('quantity')
                )['total'] or 0

                if instance.position:
                    position = instance.position
                    notes = f"Estado cambiado de {old_status} a {instance.status}. Posición asignada: {instance.position.code}"

            elif instance.status == 'en_despacho':
                # Guardar la posición que se libera al pasar a en_despacho
                if instance.position:
                    position = instance.position
                    notes = f"Estado cambiado de {old_status} a {instance.status}. Posición liberada: {instance.position.code}"

            models.BinMovement.objects.create(
                bin=instance,
                movement_type=models.BinMovementType.STATUS_CHANGED,
                old_status=old_status,
                new_status=instance.status,
                position=position,
                initial_quantity=initial_quantity,
                notes=notes
            )


@receiver(pre_save, sender=models.BinProduct)
def track_bin_product_quantity_change(sender, instance, **kwargs):
    """
    Guarda la cantidad anterior antes de guardar para poder compararla después.
    """
    if instance.pk:
        try:
            old_instance = models.BinProduct.objects.get(pk=instance.pk)
            instance._old_quantity = old_instance.quantity
        except models.BinProduct.DoesNotExist:
            instance._old_quantity = None
    else:
        instance._old_quantity = None


@receiver(post_save, sender=models.BinProduct)
def track_exit_processed_on_bin_product(sender, instance, created, **kwargs):
    """
    Trackea cuando se procesa una salida y se resta cantidad del bin.
    """
    if not created and hasattr(instance, '_old_quantity'):
        old_quantity = instance._old_quantity
        new_quantity = instance.quantity

        # Si la cantidad disminuyó, buscar ExitProducts relacionados que fueron procesados recientemente
        if old_quantity is not None and new_quantity < old_quantity:
            quantity_subtracted = old_quantity - new_quantity

            # Buscar ExitProducts relacionados que fueron procesados recientemente (últimos 5 minutos)
            from apps.order.models import ExitProduct
            from django.utils import timezone
            from datetime import timedelta

            recent_time = timezone.now() - timedelta(minutes=5)

            exit_products = ExitProduct.objects.filter(
                bin_product=instance,
                is_processed=True,
                updated_at__gte=recent_time
            ).order_by('-updated_at')

            # Si encontramos un ExitProduct procesado recientemente, crear el movimiento
            if exit_products.exists():
                exit_product = exit_products.first()
                exit_obj = exit_product.exit_product_group.exit

                # Crear el movimiento de salida procesada
                models.BinMovement.objects.create(
                    bin=instance.bin,
                    movement_type=models.BinMovementType.EXIT_PROCESSED,
                    exit=exit_obj,
                    exit_code=exit_obj.code,
                    is_picking=exit_product.is_picking,
                    quantity_subtracted=quantity_subtracted,
                    quantity_remaining=new_quantity,
                    notes=f"Salida procesada: {exit_obj.code} - {'Picking' if exit_product.is_picking else 'Completo'}"
                )
