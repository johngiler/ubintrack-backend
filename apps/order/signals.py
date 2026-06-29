from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
import os
import threading
import time

from .models import Entry, EntryDetail, Exit, ExitDetail, ExitMovement, ExitMovementType
from .services import update_entry_pdf, update_exit_pdf


# ============================================================
# Signals para tracking de movimientos de órdenes de salida
# ============================================================

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
        # Registrar creación
        ExitMovement.log_movement(
            exit_obj=instance,
            movement_type=ExitMovementType.CREATED,
            new_status=instance.status,
            notes=f'Orden de salida {instance.code} creada'
        )
    else:
        # Verificar si hubo cambio de estado
        old_status = getattr(instance, '_old_status', None)
        if old_status and old_status != instance.status:
            ExitMovement.log_movement(
                exit_obj=instance,
                movement_type=ExitMovementType.STATUS_CHANGED,
                old_status=old_status,
                new_status=instance.status,
                notes=f'Estado cambiado de {old_status} a {instance.status}'
            )


@receiver(post_save, sender=Exit)
def generate_exit_pdf_on_save(sender, instance, created, **kwargs):
    """
    Genera automáticamente el PDF de la orden de salida cuando se guarda.
    Si la orden ya tiene un detalle asociado, actualiza el PDF.
    Si no tiene detalle, crea uno nuevo con el PDF.
    """
    # Función para ejecutar la generación del PDF en un hilo separado
    def delayed_pdf_generation():
        # Si es una creación, esperar un poco para que las relaciones se creen
        if created:
            time.sleep(2)  # Esperar 2 segundos

        # Solo generar el PDF si la orden está en estado COMPLETADA o si se configura la fecha de salida
        generate_pdf = False

        if instance.status == 'completada' or instance.exit_date:
            generate_pdf = True

        if not generate_pdf:
            return

        try:
            # Recargar la instancia desde la base de datos para asegurar datos actualizados
            instance.refresh_from_db()

            # Verificar si ya existe el detalle usando try/except en lugar de hasattr
            try:
                exit_detail = instance.exit_detail
            except ExitDetail.DoesNotExist:
                # Crear el detalle si no existe
                exit_detail = ExitDetail.objects.create(exit=instance)

            # Generar o actualizar el PDF
            pdf_path = update_exit_pdf(instance)

            # Actualizar el campo pdf_document en el detalle
            if pdf_path:
                exit_detail.pdf_document = pdf_path
                exit_detail.save(update_fields=['pdf_document'])
                print(
                    f"PDF generado correctamente para orden: {instance.code} - Ruta: {pdf_path}")
            else:
                print(
                    f"ADVERTENCIA: PDF no se generó para orden {instance.code} (pdf_path es None)")
        except Exception as e:
            import traceback
            print(f"Error al generar PDF para orden {instance.code}: {e}")
            print(traceback.format_exc())

    # Iniciar un hilo para la generación del PDF
    thread = threading.Thread(target=delayed_pdf_generation)
    thread.daemon = True
    thread.start()


@receiver(post_save, sender=Entry)
def generate_entry_pdf_on_save(sender, instance, created, **kwargs):
    """
    Genera automáticamente el PDF de la orden de entrada cuando se guarda.
    Si la orden ya tiene un detalle asociado, actualiza el PDF.
    Si no tiene detalle, crea uno nuevo con el PDF.
    """
    # Función para ejecutar la generación del PDF en un hilo separado
    def delayed_pdf_generation():
        # Si es una creación, esperar un poco para que las relaciones se creen
        if created:
            time.sleep(2)  # Esperar 2 segundos

        # Solo generar el PDF si la orden está en estado COMPLETADA o si se configura la fecha de entrada
        generate_pdf = False

        if instance.status == 'completada' or instance.entry_date:
            generate_pdf = True

        if not generate_pdf:
            return

        try:
            # Recargar la instancia desde la base de datos para asegurar datos actualizados
            instance.refresh_from_db()

            # Verificar si ya existe el detalle usando try/except en lugar de hasattr
            try:
                entry_detail = instance.entry_detail
            except EntryDetail.DoesNotExist:
                # Crear el detalle si no existe
                entry_detail = EntryDetail.objects.create(entry=instance)

            # Generar o actualizar el PDF
            pdf_path = update_entry_pdf(instance)

            # Actualizar el campo pdf_document en el detalle
            if pdf_path:
                entry_detail.pdf_document = pdf_path
                entry_detail.save(update_fields=['pdf_document'])
                print(
                    f"PDF generado correctamente para orden: {instance.code} - Ruta: {pdf_path}")
            else:
                print(
                    f"ADVERTENCIA: PDF no se generó para orden {instance.code} (pdf_path es None)")
        except Exception as e:
            import traceback
            print(f"Error al generar PDF para orden {instance.code}: {e}")
            print(traceback.format_exc())

    # Iniciar un hilo para la generación del PDF
    thread = threading.Thread(target=delayed_pdf_generation)
    thread.daemon = True
    thread.start()
