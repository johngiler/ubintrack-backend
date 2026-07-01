"""
Servicios para operaciones de órdenes con transacciones atómicas y resiliencia.
"""
import os
import time
import xlwt
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.conf import settings
from django.db import connection
from django.db.models import prefetch_related_objects
from django.db.utils import OperationalError
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from .models import ExitProduct, Exit, OrderStatus, Entry, ExitMovement, ExitMovementType
from apps.inventory.models import Bin, BinMovement, BinMovementType
from apps.storage.models import Position
from apps.core import choices
from apps.common.utils.media_layout import (
    build_wms_entry_pdf_relative,
    build_wms_exit_pdf_relative,
    ensure_media_parent_dir,
)


class TransactionError(Exception):
    """Excepción para errores en transacciones"""
    pass


@transaction.atomic
def process_exit_product_atomic(exit_product_id, user=None):
    """
    Procesa un ExitProduct de forma atómica:
    1. Marca el ExitProduct como procesado
    2. Resta la cantidad del BinProduct
    3. Actualiza el estado del bin si es necesario
    4. Actualiza el estado de la orden de salida

    Si cualquier paso falla, se hace rollback de todo.

    Args:
        exit_product_id: ID del ExitProduct a procesar
        user: Usuario que realiza la operación (opcional)

    Returns:
        dict: Información sobre la operación realizada

    Raises:
        TransactionError: Si la operación falla
        ValidationError: Si hay validaciones que fallan
    """
    try:
        # Obtener el ExitProduct con todas las relaciones necesarias
        exit_product = ExitProduct.objects.select_related(
            'bin_product__bin',
            'bin_product__entry_product',
            'exit_product_group__exit'
        ).get(id=exit_product_id)

        # Validar que no esté ya procesado
        if exit_product.is_processed:
            raise ValidationError('Este producto de salida ya está procesado')

        # Validar que el bin tenga suficiente cantidad o buscar alternativa
        bin_product = exit_product.bin_product
        quantity_needed = exit_product.quantity
        original_bin_product = bin_product
        was_reassigned = False

        # Si no hay suficiente cantidad, intentar reasignar
        if bin_product.quantity < quantity_needed:
            # Buscar bin alternativo con protección contra condiciones de carrera
            # ESTRATEGIA: Priorizar bins ya en uso (picking) para aprovechar cantidades sobrantes
            from apps.inventory.models import BinProduct
            product = bin_product.entry_product.product
            client = exit_product.exit_product_group.exit.client
            original_batch = bin_product.entry_product.batch

            # Buscar bins completos en otras salidas para excluirlos
            bins_completos_en_otras_salidas = ExitProduct.objects.filter(
                exit_product_group__exit__client=client,
                exit_product_group__exit__status__in=[
                    'pendiente', 'en_progreso'],
                bin_product__entry_product__product=product,
                is_picking=False
            ).exclude(
                id=exit_product.id
            ).values_list('bin_product__bin_id', flat=True)

            # FASE 1: Buscar bins que ya están siendo usados (picking bins)
            # Estos son bins que tienen ExitProducts con is_picking=True, es decir, ya tienen cantidades sobrantes
            picking_bin_ids = ExitProduct.objects.filter(
                exit_product_group__exit__client=client,
                exit_product_group__exit__status__in=[
                    'pendiente', 'en_progreso'],
                bin_product__entry_product__product=product,
                is_picking=True  # Solo bins que ya están siendo usados parcialmente
            ).exclude(
                id=exit_product.id
            ).values_list('bin_product__bin_id', flat=True).distinct()

            # Query base para bins disponibles
            base_query = BinProduct.objects.filter(
                entry_product__product=product,
                entry_product__entry__client=client,
                bin__status=choices.BinStatus.ALMACENADO,
                bin__is_active=True,
                quantity__gte=quantity_needed
            ).exclude(
                bin_id__in=bins_completos_en_otras_salidas
            ).select_related('bin', 'entry_product')

            # Priorizar bins picking (ya en uso) del mismo lote
            picking_bins_query = base_query.filter(bin_id__in=picking_bin_ids)
            new_bins_query = base_query.exclude(bin_id__in=picking_bin_ids)

            alternative_bin = None

            # FASE 1: Intentar primero con bins picking (ya en uso) - esperar un poco si están bloqueados
            if picking_bin_ids.exists():
                if original_batch:
                    same_batch_picking = picking_bins_query.filter(
                        entry_product__batch=original_batch
                    ).order_by('entry_product__expiration_date')
                    other_picking = picking_bins_query.exclude(
                        entry_product__batch=original_batch
                    ).order_by('entry_product__expiration_date')
                    picking_candidates = list(
                        same_batch_picking) + list(other_picking)
                else:
                    picking_candidates = list(picking_bins_query.order_by(
                        'entry_product__expiration_date'))

                # Intentar bloquear bins picking con reintentos (para aprovechar cantidades sobrantes)
                # Estrategia: Reintentar con pausas cortas para dar oportunidad a que otros operadores terminen
                # Limitar a 5 bins picking
                for candidate in picking_candidates[:5]:
                    max_retries = 3
                    retry_delay = 0.1  # 100ms entre reintentos

                    for retry in range(max_retries):
                        try:
                            # Intentar bloquear el bin picking
                            # Si está bloqueado, reintentar después de una pausa corta
                            locked_bin = BinProduct.objects.select_for_update(
                                nowait=True  # No esperar, pero reintentaremos
                            ).get(
                                id=candidate.id
                            )

                            # Revalidar después del bloqueo
                            if locked_bin.bin.status != choices.BinStatus.ALMACENADO:
                                break  # Salir del loop de reintentos, probar siguiente candidato
                            if locked_bin.quantity < quantity_needed:
                                break  # Salir del loop de reintentos, probar siguiente candidato

                            # Bin picking válido encontrado, usar este
                            alternative_bin = locked_bin
                            break  # Salir del loop de reintentos

                        except (OperationalError, transaction.TransactionManagementError):
                            # Bin bloqueado, esperar un poco y reintentar
                            if retry < max_retries - 1:
                                time.sleep(retry_delay)
                                continue
                            else:
                                # Ya se agotaron los reintentos, probar siguiente candidato
                                break
                        except BinProduct.DoesNotExist:
                            break  # Bin ya no existe, probar siguiente
                        except Exception:
                            break  # Error inesperado, probar siguiente

                    # Si encontramos un bin válido, salir del loop principal
                    if alternative_bin:
                        break

            # FASE 2: Si no se encontró bin picking disponible, buscar bins nuevos (completos)
            if not alternative_bin:
                if original_batch:
                    same_batch_new = new_bins_query.filter(
                        entry_product__batch=original_batch
                    ).order_by('entry_product__expiration_date')
                    other_new = new_bins_query.exclude(
                        entry_product__batch=original_batch
                    ).order_by('entry_product__expiration_date')
                    new_candidates = list(same_batch_new) + list(other_new)
                else:
                    new_candidates = list(new_bins_query.order_by(
                        'entry_product__expiration_date'))

                # Intentar bloquear bins nuevos con nowait=True (no esperar)
                for candidate in new_candidates[:10]:  # Limitar intentos
                    try:
                        locked_bin = BinProduct.objects.select_for_update(
                            nowait=True  # No esperar, si está bloqueado pasar al siguiente
                        ).get(
                            id=candidate.id
                        )

                        # Revalidar después del bloqueo
                        if locked_bin.bin.status != choices.BinStatus.ALMACENADO:
                            continue
                        if locked_bin.quantity < quantity_needed:
                            continue

                        # Bin nuevo válido encontrado
                        alternative_bin = locked_bin
                        break

                    except (OperationalError, transaction.TransactionManagementError):
                        # Bin bloqueado, continuar con siguiente
                        continue
                    except BinProduct.DoesNotExist:
                        continue
                    except Exception:
                        continue

            if alternative_bin:
                # Reasignar a nuevo bin
                # Calcular la cantidad que quedará después de restar
                new_quantity_after_subtraction = alternative_bin.quantity - quantity_needed
                # Actualizar is_picking: si quedará cantidad, es picking; si se agota, es completo
                exit_product.is_picking = new_quantity_after_subtraction > 0
                exit_product.bin_product = alternative_bin
                exit_product.save(
                    update_fields=['bin_product', 'is_picking'])
                bin_product = alternative_bin
                was_reassigned = True
            else:
                raise ValidationError(
                    f'Cantidad insuficiente en el bin. Disponible: {original_bin_product.quantity}, '
                    f'Necesario: {quantity_needed}. No se encontraron bins alternativos disponibles '
                    f'(posiblemente todos fueron asignados simultáneamente por otros operadores).'
                )

        # Guardar estado anterior para posible rollback
        old_bin_quantity = bin_product.quantity
        old_bin_status = bin_product.bin.status
        old_exit_product_processed = exit_product.is_processed

        # 1. Marcar ExitProduct como procesado
        exit_product.is_processed = True
        if user:
            exit_product.updated_by = user
        exit_product.save(
            update_fields=['is_processed', 'updated_by', 'updated_at'])

        # 2. Restar cantidad del BinProduct
        new_quantity = old_bin_quantity - quantity_needed
        bin_product.quantity = new_quantity
        if user:
            bin_product.updated_by = user
        bin_product.save(
            update_fields=['quantity', 'updated_by', 'updated_at'])

        # 2.5. Actualizar el inventario del EntryProduct explícitamente
        # Esto asegura que in_inventory refleje la cantidad total almacenada
        # (cantidad actual en bins + cantidad despachada)
        if bin_product.entry_product:
            bin_product.entry_product.update_inventory_status()

        # 3. Actualizar estado del bin si corresponde
        # IMPORTANTE: Un bin solo puede cambiar a EN_DESPACHO o DESPACHADO si su cantidad es 0
        # No importa si es "completo" o "picking", si tiene cantidad > 0, debe permanecer en ALMACENADO
        bin = bin_product.bin
        if new_quantity == 0 and bin.status == choices.BinStatus.ALMACENADO:
            # Solo cambiar a EN_DESPACHO si la cantidad llegó a 0
            bin.status = choices.BinStatus.EN_DESPACHO
            if user:
                bin.updated_by = user
            bin.save(
                update_fields=['status', 'updated_by', 'updated_at'])

        # 4. Actualizar estado de la orden de salida si es necesario
        exit_obj = exit_product.exit_product_group.exit
        if exit_obj.status != OrderStatus.EN_PROGRESO:
            exit_obj.status = OrderStatus.EN_PROGRESO
            if user:
                exit_obj.updated_by = user
            exit_obj.save(update_fields=['status', 'updated_by', 'updated_at'])

        # Nota: Los bins pasan a DESPACHADO solo cuando la orden se marca como COMPLETADA,
        # no automáticamente al procesar todos los productos.
        # La lógica de despacho está en Exit.save() cuando status cambia a 'completada'

        # Registrar movimiento de bin procesado
        product = bin_product.entry_product.product if bin_product.entry_product else None
        ExitMovement.log_movement(
            exit_obj=exit_obj,
            movement_type=ExitMovementType.BIN_PROCESSED,
            product=product,
            product_sku=product.client_sku if product else None,
            product_name=product.name if product else None,
            bin=bin,
            bin_code=bin.code,
            quantity=quantity_needed,
            is_picking=exit_product.is_picking,
            notes=f'Bin {bin.code} procesado. Cantidad restada: {quantity_needed}. Restante en bin: {new_quantity}'
        )

        # Si fue reasignado automáticamente, registrar ese movimiento también
        if was_reassigned:
            ExitMovement.log_movement(
                exit_obj=exit_obj,
                movement_type=ExitMovementType.BIN_REASSIGNED,
                product=product,
                product_sku=product.client_sku if product else None,
                product_name=product.name if product else None,
                old_bin_code=original_bin_product.bin.code,
                new_bin_code=bin.code,
                quantity=quantity_needed,
                notes=f'Reasignación automática durante procesamiento: {original_bin_product.bin.code} → {bin.code} (cantidad insuficiente en bin original)'
            )

        return {
            'success': True,
            'exit_product_id': exit_product.id,
            'bin_code': bin.code,
            'quantity_subtracted': quantity_needed,
            'quantity_remaining': new_quantity,
            'bin_status': bin.status,
            'exit_status': exit_obj.status,
            'was_reassigned': was_reassigned,
            'original_bin_code': original_bin_product.bin.code if was_reassigned else None,
        }

    except ExitProduct.DoesNotExist:
        raise ValidationError(
            f'ExitProduct con ID {exit_product_id} no encontrado')
    except ValidationError as e:
        # Re-lanzar ValidationError preservando los datos de inconsistencia
        raise e
    except Exception as e:
        # Cualquier error hará rollback automático por @transaction.atomic
        raise TransactionError(f'Error al procesar salida: {str(e)}') from e


def process_exit_products_bulk(exit_product_group_id, user=None):
    """
    Procesa todos los ExitProducts no procesados de un ExitProductGroup.
    Cada ExitProduct se procesa en su propia transacción para evitar que
    un error en uno afecte a los demás.

    Args:
        exit_product_group_id: ID del ExitProductGroup
        user: Usuario que realiza la operación (opcional)

    Returns:
        dict: Resumen de la operación con éxitos y errores
    """
    from .models import ExitProductGroup

    try:
        group = ExitProductGroup.objects.select_related(
            'exit', 'product'
        ).prefetch_related(
            'exit_products__bin_product__bin'
        ).get(id=exit_product_group_id)
    except ExitProductGroup.DoesNotExist:
        raise ValidationError(f'ExitProductGroup con ID {exit_product_group_id} no encontrado')

    # Verificar que la orden esté en estado válido
    if group.exit.status not in [OrderStatus.PENDIENTE, OrderStatus.EN_PROGRESO]:
        raise ValidationError(
            f'No se puede procesar bins de una orden en estado "{group.exit.get_status_display()}"'
        )

    # Obtener todos los exit_products no procesados
    unprocessed_products = list(group.exit_products.filter(is_processed=False))

    if not unprocessed_products:
        return {
            'success': True,
            'message': 'No hay bins pendientes de procesar',
            'total': 0,
            'processed': 0,
            'failed': 0,
            'results': []
        }

    results = []
    processed_count = 0
    failed_count = 0

    for exit_product in unprocessed_products:
        try:
            # Procesar cada exit_product en su propia transacción
            result = process_exit_product_atomic(exit_product.id, user)
            results.append({
                'exit_product_id': exit_product.id,
                'bin_code': result.get('bin_code'),
                'success': True,
                'quantity': result.get('quantity_subtracted'),
                'was_reassigned': result.get('was_reassigned', False)
            })
            processed_count += 1
        except (ValidationError, TransactionError) as e:
            results.append({
                'exit_product_id': exit_product.id,
                'bin_code': exit_product.bin_product.bin.code if exit_product.bin_product else 'N/A',
                'success': False,
                'error': str(e)
            })
            failed_count += 1

    return {
        'success': failed_count == 0,
        'message': f'Procesados {processed_count} de {len(unprocessed_products)} bins',
        'total': len(unprocessed_products),
        'processed': processed_count,
        'failed': failed_count,
        'results': results
    }


@transaction.atomic
def reassign_exit_product(exit_product_id, user=None):
    """
    Reasigna un ExitProduct a un bin alternativo SIN procesarlo.
    Útil cuando el usuario quiere reasignar manualmente antes de procesar.

    Args:
        exit_product_id: ID del ExitProduct a reasignar
        user: Usuario que realiza la operación (opcional)

    Returns:
        dict: Información sobre la reasignación realizada

    Raises:
        ValidationError: Si no hay bin alternativo o ya está procesado
    """
    from apps.inventory.models import BinProduct

    try:
        exit_product = ExitProduct.objects.select_related(
            'bin_product__bin',
            'bin_product__entry_product',
            'exit_product_group__exit'
        ).get(id=exit_product_id)

        if exit_product.is_processed:
            raise ValidationError('Este producto de salida ya está procesado')

        bin_product = exit_product.bin_product
        quantity_needed = exit_product.quantity
        original_bin_code = bin_product.bin.code
        product = bin_product.entry_product.product
        client = exit_product.exit_product_group.exit.client
        original_batch = bin_product.entry_product.batch

        # Buscar bins completos en otras salidas para excluirlos
        bins_completos_en_otras_salidas = ExitProduct.objects.filter(
            exit_product_group__exit__client=client,
            exit_product_group__exit__status__in=['pendiente', 'en_progreso'],
            bin_product__entry_product__product=product,
            is_picking=False
        ).exclude(
            id=exit_product.id
        ).values_list('bin_product__bin_id', flat=True)

        # Buscar bins picking (ya en uso)
        picking_bin_ids = ExitProduct.objects.filter(
            exit_product_group__exit__client=client,
            exit_product_group__exit__status__in=['pendiente', 'en_progreso'],
            bin_product__entry_product__product=product,
            is_picking=True
        ).exclude(
            id=exit_product.id
        ).values_list('bin_product__bin_id', flat=True).distinct()

        # Query base para bins disponibles
        base_query = BinProduct.objects.filter(
            entry_product__product=product,
            entry_product__entry__client=client,
            bin__status=choices.BinStatus.ALMACENADO,
            bin__is_active=True,
            quantity__gte=quantity_needed
        ).exclude(
            bin_id__in=bins_completos_en_otras_salidas
        ).exclude(
            id=bin_product.id  # Excluir el bin actual
        ).select_related('bin', 'bin__position', 'entry_product')

        # Priorizar bins picking del mismo lote
        picking_bins_query = base_query.filter(bin_id__in=picking_bin_ids)
        new_bins_query = base_query.exclude(bin_id__in=picking_bin_ids)

        alternative_bin = None

        # FASE 1: Buscar en bins picking (ya en uso)
        if picking_bin_ids.exists():
            if original_batch:
                same_batch_picking = picking_bins_query.filter(
                    entry_product__batch=original_batch
                ).order_by('entry_product__expiration_date')
                other_picking = picking_bins_query.exclude(
                    entry_product__batch=original_batch
                ).order_by('entry_product__expiration_date')
                picking_candidates = list(
                    same_batch_picking) + list(other_picking)
            else:
                picking_candidates = list(picking_bins_query.order_by(
                    'entry_product__expiration_date'))

            for candidate in picking_candidates[:5]:
                try:
                    locked_bin = BinProduct.objects.select_for_update(
                        nowait=True
                    ).get(id=candidate.id)

                    if locked_bin.bin.status != choices.BinStatus.ALMACENADO:
                        continue
                    if locked_bin.quantity < quantity_needed:
                        continue

                    alternative_bin = locked_bin
                    break
                except (OperationalError, transaction.TransactionManagementError):
                    continue
                except BinProduct.DoesNotExist:
                    continue

        # FASE 2: Buscar en bins nuevos
        if not alternative_bin:
            if original_batch:
                same_batch_new = new_bins_query.filter(
                    entry_product__batch=original_batch
                ).order_by('entry_product__expiration_date')
                other_new = new_bins_query.exclude(
                    entry_product__batch=original_batch
                ).order_by('entry_product__expiration_date')
                new_candidates = list(same_batch_new) + list(other_new)
            else:
                new_candidates = list(new_bins_query.order_by(
                    'entry_product__expiration_date'))

            for candidate in new_candidates[:10]:
                try:
                    locked_bin = BinProduct.objects.select_for_update(
                        nowait=True
                    ).get(id=candidate.id)

                    if locked_bin.bin.status != choices.BinStatus.ALMACENADO:
                        continue
                    if locked_bin.quantity < quantity_needed:
                        continue

                    alternative_bin = locked_bin
                    break
                except (OperationalError, transaction.TransactionManagementError):
                    continue
                except BinProduct.DoesNotExist:
                    continue

        if not alternative_bin:
            raise ValidationError(
                f'No se encontró un bin alternativo disponible con al menos {quantity_needed} unidades.'
            )

        # Reasignar
        new_quantity_after_subtraction = alternative_bin.quantity - quantity_needed
        exit_product.is_picking = new_quantity_after_subtraction > 0
        exit_product.bin_product = alternative_bin
        if user:
            exit_product.updated_by = user
        exit_product.save(
            update_fields=['bin_product', 'is_picking', 'updated_by', 'updated_at'])

        # Registrar movimiento de reasignación manual
        exit_obj = exit_product.exit_product_group.exit
        ExitMovement.log_movement(
            exit_obj=exit_obj,
            movement_type=ExitMovementType.BIN_REASSIGNED,
            product=product,
            product_sku=product.client_sku if product else None,
            product_name=product.name if product else None,
            old_bin_code=original_bin_code,
            new_bin_code=alternative_bin.bin.code,
            quantity=quantity_needed,
            notes=f'Reasignación manual: {original_bin_code} → {alternative_bin.bin.code}'
        )

        return {
            'success': True,
            'message': f'Bin reasignado exitosamente de {original_bin_code} a {alternative_bin.bin.code}',
            'original_bin_code': original_bin_code,
            'new_bin_code': alternative_bin.bin.code,
            'new_bin_position': alternative_bin.bin.position.code if alternative_bin.bin.position else None,
            'new_bin_quantity': alternative_bin.quantity,
            'same_batch': alternative_bin.entry_product.batch == original_batch if original_batch else False
        }

    except ExitProduct.DoesNotExist:
        raise ValidationError(
            f'ExitProduct con ID {exit_product_id} no encontrado')
    except ValidationError:
        raise
    except Exception as e:
        raise TransactionError(f'Error al reasignar bin: {str(e)}') from e


@transaction.atomic
def replace_exit_product_bin(exit_product_id, new_bin_code, user=None):
    """
    Reemplaza el bin de un ExitProduct por otro bin especificado mediante código QR.

    Validaciones:
    - El ExitProduct no debe estar procesado
    - El nuevo bin debe existir y estar activo/almacenado
    - El nuevo bin debe ser del mismo cliente
    - El nuevo bin debe tener el mismo producto (SKU)
    - El nuevo bin debe tener suficiente cantidad disponible
    - El nuevo bin no debe estar ya asignado como bin completo a otra salida

    Args:
        exit_product_id: ID del ExitProduct a modificar
        new_bin_code: Código del bin escaneado (ej: "BIN-0000001234")
        user: Usuario que realiza la operación (opcional)

    Returns:
        dict: Información sobre el reemplazo realizado

    Raises:
        ValidationError: Si alguna validación falla
        TransactionError: Si la operación falla
    """
    from apps.inventory.models import BinProduct, Bin
    from django.db.models import Sum

    try:
        # 1. Obtener el ExitProduct actual
        exit_product = ExitProduct.objects.select_related(
            'bin_product__bin',
            'bin_product__entry_product__product',
            'exit_product_group__exit__client'
        ).get(id=exit_product_id)

        if exit_product.is_processed:
            raise ValidationError(
                'Este producto de salida ya está procesado y no puede ser reemplazado.')

        original_bin_product = exit_product.bin_product
        original_bin_code = original_bin_product.bin.code
        quantity_needed = exit_product.quantity
        expected_product = original_bin_product.entry_product.product
        expected_client = exit_product.exit_product_group.exit.client
        exit_id = exit_product.exit_product_group.exit_id

        # 2. Buscar el nuevo bin por código
        try:
            new_bin_entry = Bin.objects.select_related(
                'client', 'position').get(code=new_bin_code)
        except Bin.DoesNotExist:
            raise ValidationError(
                f'Bin con código "{new_bin_code}" no encontrado.')

        # 2.1 Validar que no sea el mismo bin
        if new_bin_entry.id == original_bin_product.bin.id:
            raise ValidationError(
                f'El bin {new_bin_code} es el mismo bin actual. Seleccione un bin diferente.')

        # 3. Validar que el bin esté activo y almacenado
        if not new_bin_entry.is_active:
            raise ValidationError(f'El bin {new_bin_code} no está activo.')

        if new_bin_entry.status != choices.BinStatus.ALMACENADO:
            status_display = dict(choices.BinStatus.choices).get(
                new_bin_entry.status, new_bin_entry.status)
            raise ValidationError(
                f'El bin {new_bin_code} no está almacenado (estado actual: {status_display}).')

        # 4. Validar que sea del mismo cliente
        if new_bin_entry.client_id != expected_client.id:
            raise ValidationError(
                f'El bin {new_bin_code} pertenece a otro cliente.')

        # 5. Buscar el BinProduct con el mismo producto en el nuevo bin
        new_bin_product = BinProduct.objects.filter(
            bin=new_bin_entry,
            entry_product__product=expected_product
        ).select_related('entry_product', 'entry_product__product').first()

        if not new_bin_product:
            raise ValidationError(
                f'El bin {new_bin_code} no contiene el producto {expected_product.name} ({expected_product.client_sku}).'
            )

        # 6. Validar cantidad contra la cantidad física del bin
        # No restamos reservas de otras órdenes - permite compartir bin entre órdenes
        # La validación de sobre-reservas ocurre al momento de procesar
        if new_bin_product.quantity < quantity_needed:
            raise ValidationError(
                f'El bin {new_bin_code} no tiene suficiente cantidad. '
                f'Se requieren {quantity_needed}, disponible en el bin: {new_bin_product.quantity}.'
            )

        # 7. Verificar si el nuevo bin ya existe en esta orden (cualquier grupo del mismo producto)
        # NOTA: Se permite compartir bins entre órdenes - la validación de disponibilidad
        # real ocurre al momento de procesar (y reasigna automáticamente si hay conflicto)
        existing_exit_product_same_bin = ExitProduct.objects.filter(
            bin_product=new_bin_product,
            exit_product_group__exit_id=exit_id,  # Misma orden
            exit_product_group__product=expected_product,  # Mismo producto
            is_processed=False
        ).exclude(
            id=exit_product_id
        ).first()

        # Obtener información del lote para la respuesta
        original_batch = original_bin_product.entry_product.batch
        new_batch = new_bin_product.entry_product.batch

        if existing_exit_product_same_bin:
            # 8.1 El bin ya existe en el mismo grupo - SUMAR cantidades
            new_total_quantity = existing_exit_product_same_bin.quantity + quantity_needed

            # Verificar que la suma no exceda la cantidad disponible en el bin
            if new_total_quantity > new_bin_product.quantity:
                raise ValidationError(
                    f'La suma de cantidades ({new_total_quantity}) excede la cantidad disponible '
                    f'en el bin {new_bin_code} ({new_bin_product.quantity}).'
                )

            # Actualizar la cantidad del ExitProduct existente
            existing_exit_product_same_bin.quantity = new_total_quantity
            # Determinar si es picking
            existing_exit_product_same_bin.is_picking = new_total_quantity < new_bin_product.quantity
            if user:
                existing_exit_product_same_bin.updated_by = user
            existing_exit_product_same_bin.save(
                update_fields=['quantity', 'is_picking', 'updated_by', 'updated_at'])

            # Eliminar el ExitProduct original (el que estamos reemplazando)
            exit_product.delete()

            # Registrar movimiento de reemplazo con fusión
            exit_obj = existing_exit_product_same_bin.exit_product_group.exit
            ExitMovement.log_movement(
                exit_obj=exit_obj,
                movement_type=ExitMovementType.BIN_REPLACED,
                product=expected_product,
                product_sku=expected_product.client_sku if expected_product else None,
                product_name=expected_product.name if expected_product else None,
                old_bin_code=original_bin_code,
                new_bin_code=new_bin_code,
                quantity=quantity_needed,
                new_quantity=new_total_quantity,
                notes=f'Reemplazo con fusión: {original_bin_code} → {new_bin_code}. Cantidad añadida: {quantity_needed}. Total en bin: {new_total_quantity}',
                metadata={'merged': True, 'new_batch': new_batch}
            )

            return {
                'success': True,
                'message': f'Cantidad sumada al bin existente {new_bin_code} (ahora {new_total_quantity} unidades)',
                'merged': True,
                'original_bin_code': original_bin_code,
                'new_bin_code': new_bin_code,
                'new_bin_position': new_bin_entry.position.code if new_bin_entry.position else None,
                'new_bin_quantity': new_bin_product.quantity,
                'new_total_assigned': new_total_quantity,
                'quantity_added': quantity_needed,
                'is_picking': existing_exit_product_same_bin.is_picking,
                'same_batch': original_batch == new_batch if original_batch and new_batch else False,
                'new_batch': new_batch,
                'new_expiration_date': new_bin_product.entry_product.expiration_date.strftime('%d/%m/%Y') if new_bin_product.entry_product.expiration_date else None
            }
        else:
            # 8.2 El bin no existe en el grupo - Realizar reemplazo normal
            # Determinar si es picking (cantidad parcial)
            new_quantity_after_subtraction = new_bin_product.quantity - quantity_needed
            exit_product.is_picking = new_quantity_after_subtraction > 0
            exit_product.bin_product = new_bin_product
            if user:
                exit_product.updated_by = user
            exit_product.save(
                update_fields=['bin_product', 'is_picking', 'updated_by', 'updated_at'])

            # Registrar movimiento de reemplazo
            exit_obj = exit_product.exit_product_group.exit
            ExitMovement.log_movement(
                exit_obj=exit_obj,
                movement_type=ExitMovementType.BIN_REPLACED,
                product=expected_product,
                product_sku=expected_product.client_sku if expected_product else None,
                product_name=expected_product.name if expected_product else None,
                old_bin_code=original_bin_code,
                new_bin_code=new_bin_code,
                quantity=quantity_needed,
                is_picking=exit_product.is_picking,
                notes=f'Reemplazo manual: {original_bin_code} → {new_bin_code}. Cantidad: {quantity_needed}',
                metadata={'merged': False, 'new_batch': new_batch}
            )

            return {
                'success': True,
                'message': f'Bin reemplazado exitosamente de {original_bin_code} a {new_bin_code}',
                'merged': False,
                'original_bin_code': original_bin_code,
                'new_bin_code': new_bin_code,
                'new_bin_position': new_bin_entry.position.code if new_bin_entry.position else None,
                'new_bin_quantity': new_bin_product.quantity,
                'quantity_assigned': quantity_needed,
                'is_picking': exit_product.is_picking,
                'same_batch': original_batch == new_batch if original_batch and new_batch else False,
                'new_batch': new_batch,
                'new_expiration_date': new_bin_product.entry_product.expiration_date.strftime('%d/%m/%Y') if new_bin_product.entry_product.expiration_date else None
            }

    except ExitProduct.DoesNotExist:
        raise ValidationError(
            f'ExitProduct con ID {exit_product_id} no encontrado.')
    except ValidationError:
        raise
    except Exception as e:
        raise TransactionError(f'Error al reemplazar bin: {str(e)}') from e


@transaction.atomic
def reverse_exit_product_atomic(exit_product_id, user=None):
    """
    Reversa un ExitProduct procesado de forma atómica:
    1. Marca el ExitProduct como NO procesado
    2. Restaura la cantidad al BinProduct
    3. Restaura el estado del bin si es necesario (para bins completos)
    4. Restaura la posición del bin si es necesario (para bins completos)
    5. Crea un registro de movimiento de reversión

    Args:
        exit_product_id: ID del ExitProduct a reversar
        user: Usuario que realiza la operación (opcional)

    Returns:
        dict: Información sobre la operación realizada

    Raises:
        TransactionError: Si la operación falla
        ValidationError: Si hay validaciones que fallan
    """
    try:
        # Obtener el ExitProduct con todas las relaciones necesarias
        exit_product = ExitProduct.objects.select_related(
            'bin_product__bin__position',
            'bin_product__entry_product__product',
            'exit_product_group__exit'
        ).get(id=exit_product_id)

        # Validar que esté procesado
        if not exit_product.is_processed:
            raise ValidationError('Este producto de salida no está procesado')

        # Validar que la salida esté en_progreso
        exit_obj = exit_product.exit_product_group.exit
        if exit_obj.status != OrderStatus.EN_PROGRESO:
            raise ValidationError(
                'Solo se pueden reversar productos de salidas en progreso')

        bin_product = exit_product.bin_product
        bin = bin_product.bin
        quantity_to_restore = exit_product.quantity
        is_picking = exit_product.is_picking

        # Guardar estados anteriores para el movimiento
        old_bin_status = bin.status
        old_quantity = bin_product.quantity

        # 1. Marcar ExitProduct como NO procesado
        exit_product.is_processed = False
        if user:
            exit_product.updated_by = user
        exit_product.save(
            update_fields=['is_processed', 'updated_by', 'updated_at'])

        # 2. Restaurar cantidad del BinProduct
        new_quantity = old_quantity + quantity_to_restore
        bin_product.quantity = new_quantity
        if user:
            bin_product.updated_by = user
        bin_product.save(
            update_fields=['quantity', 'updated_by', 'updated_at'])

        # 2.5. Actualizar el inventario del EntryProduct
        if bin_product.entry_product:
            bin_product.entry_product.update_inventory_status()

        # Variables para tracking
        position_restored = None
        status_restored = False

        # 3. Para bins completos o picking con cantidad 0: restaurar estado y posición
        if not is_picking:
            # Bin completo: restaurar a ALMACENADO si está en EN_DESPACHO o DESPACHADO
            if bin.status in [choices.BinStatus.EN_DESPACHO, choices.BinStatus.DESPACHADO]:
                # Buscar el último movimiento que tenga la posición guardada
                last_position_movement = BinMovement.objects.filter(
                    bin=bin,
                    position__isnull=False
                ).order_by('-created_at').first()

                if last_position_movement and last_position_movement.position:
                    position_to_restore = last_position_movement.position

                    # Verificar que la posición esté libre
                    if position_to_restore.status == choices.PositionStatus.LIBRE:
                        # Restaurar posición al bin
                        bin.position = position_to_restore
                        position_restored = position_to_restore

                        # Actualizar estado de la posición a OCUPADO
                        Position.objects.filter(pk=position_to_restore.pk).update(
                            status=choices.PositionStatus.OCUPADO,
                            updated_at=timezone.now()
                        )
                    else:
                        raise ValidationError(
                            f'La posición {position_to_restore.code} ya no está disponible (estado: {position_to_restore.status})')

                # Restaurar estado del bin a ALMACENADO
                bin.status = choices.BinStatus.ALMACENADO
                bin.dispatched_date = None
                status_restored = True
                if user:
                    bin.updated_by = user
                bin.save(update_fields=[
                               'status', 'position', 'dispatched_date', 'updated_by', 'updated_at'])
        else:
            # Bin picking: si estaba en EN_DESPACHO (porque cantidad llegó a 0), restaurar a ALMACENADO
            if bin.status == choices.BinStatus.EN_DESPACHO and old_quantity == 0:
                # Buscar posición anterior
                last_position_movement = BinMovement.objects.filter(
                    bin=bin,
                    position__isnull=False
                ).order_by('-created_at').first()

                if last_position_movement and last_position_movement.position:
                    position_to_restore = last_position_movement.position

                    if position_to_restore.status == choices.PositionStatus.LIBRE:
                        bin.position = position_to_restore
                        position_restored = position_to_restore

                        Position.objects.filter(pk=position_to_restore.pk).update(
                            status=choices.PositionStatus.OCUPADO,
                            updated_at=timezone.now()
                        )

                bin.status = choices.BinStatus.ALMACENADO
                status_restored = True
                if user:
                    bin.updated_by = user
                bin.save(
                    update_fields=['status', 'position', 'updated_by', 'updated_at'])

        # 4. Crear movimiento de reversión en bin
        BinMovement.objects.create(
            bin=bin,
            movement_type=BinMovementType.EXIT_REVERSED,
            old_status=old_bin_status,
            new_status=bin.status,
            exit=exit_obj,
            exit_code=exit_obj.code,
            is_picking=is_picking,
            quantity_subtracted=quantity_to_restore,  # Cantidad restaurada
            quantity_remaining=new_quantity,
            position=position_restored,
            notes=f'Reversión de operación. Cantidad restaurada: {quantity_to_restore}.'
        )

        # 5. Registrar movimiento en la orden de salida
        product = bin_product.entry_product.product if bin_product.entry_product else None
        ExitMovement.log_movement(
            exit_obj=exit_obj,
            movement_type=ExitMovementType.BIN_REVERSED,
            product=product,
            product_sku=product.client_sku if product else None,
            product_name=product.name if product else None,
            bin=bin,
            bin_code=bin.code,
            quantity=quantity_to_restore,
            is_picking=is_picking,
            notes=f'Bin {bin.code} reversado. Cantidad restaurada: {quantity_to_restore}. ' +
            (f'Posición {position_restored.code} restaurada.' if position_restored else '')
        )

        return {
            'success': True,
            'exit_product_id': exit_product.id,
            'bin_code': bin.code,
            'quantity_restored': quantity_to_restore,
            'quantity_current': new_quantity,
            'bin_status': bin.status,
            'status_restored': status_restored,
            'position_restored': position_restored.code if position_restored else None,
            'is_picking': is_picking,
        }

    except ExitProduct.DoesNotExist:
        raise ValidationError(
            f'ExitProduct con ID {exit_product_id} no encontrado')
    except ValidationError as e:
        raise e
    except Exception as e:
        raise TransactionError(f'Error al reversar salida: {str(e)}') from e


@transaction.atomic
def update_bin_status_atomic(bin_entry_id, new_status, user=None, was_qr_validated=False):
    """
    Actualiza el estado de un bin de forma atómica.

    Args:
        bin_entry_id: ID del Bin
        new_status: Nuevo estado
        user: Usuario que realiza la operación
        was_qr_validated: Si fue validado con QR

    Returns:
        Bin: El bin actualizado

    Raises:
        TransactionError: Si la operación falla
    """
    try:
        bin = Bin.objects.select_related(
            'position').get(id=bin_entry_id)

        old_status = bin.status

        # Actualizar estado
        bin.status = new_status
        bin.was_qr_validated = was_qr_validated

        if new_status == choices.BinStatus.DESPACHADO and not bin.dispatched_date:
            bin.dispatched_date = timezone.now()

        if user:
            bin.updated_by = user

        bin.save()

        # Actualizar posición si es necesario
        if bin.position:
            from apps.storage.models import Position
            if new_status == choices.BinStatus.ALMACENADO:
                new_position_status = choices.PositionStatus.OCUPADO
            elif new_status in [choices.BinStatus.EN_DESPACHO, choices.BinStatus.DESPACHADO]:
                new_position_status = choices.PositionStatus.LIBRE
            else:
                new_position_status = None

            if new_position_status:
                # Usar update() directo para evitar validación de FK de created_by
                Position.objects.filter(pk=bin.position.pk).update(
                    status=new_position_status,
                    updated_at=timezone.now()
                )

        return bin

    except Bin.DoesNotExist:
        raise ValidationError(f'Bin con ID {bin_entry_id} no encontrado')
    except Exception as e:
        raise TransactionError(
            f'Error al actualizar estado del bin: {str(e)}') from e


def generate_exit_pdf(exit_instance):
    """
    Genera un PDF para la orden de salida con la información requerida.

    Args:
        exit_instance: Instancia del modelo Exit

    Returns:
        str: Ruta relativa al archivo PDF generado
    """
    # Forzar sincronización con la base de datos
    connection.ensure_connection()

    # Recargar la instancia para asegurarnos de tener datos actualizados
    exit_instance.refresh_from_db()

    # Pre-cargar las relaciones para evitar consultas N+1
    prefetch_related_objects([exit_instance],
                             'exit_product_groups',
                             'exit_product_groups__product',
                             'exit_product_groups__exit_products',
                             'exit_product_groups__exit_products__bin_product',
                             'exit_product_groups__exit_products__bin_product__bin')

    # Verificar que la instancia tiene grupos de productos
    groups_count = exit_instance.exit_product_groups.count()
    print(
        f"DEBUG: La orden de salida {exit_instance.code} tiene {groups_count} grupos de productos")

    if groups_count == 0:
        print(
            f"ADVERTENCIA: La orden de salida {exit_instance.code} no tiene grupos de productos. Puede que el PDF se genere vacío.")

    relative_path = build_wms_exit_pdf_relative(exit_instance)
    filepath = ensure_media_parent_dir(relative_path)
    filename = os.path.basename(relative_path)

    # Configuración del documento
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=0.75*inch,
        bottomMargin=0.75*inch,
        leftMargin=0.75*inch,
        rightMargin=0.75*inch
    )

    # Estilos para el documento
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='CenterTitle', fontSize=16,
               alignment=TA_CENTER, spaceAfter=12))
    styles.add(ParagraphStyle(name='LeftHeading',
               fontSize=12, alignment=TA_LEFT, spaceBefore=6))
    styles.add(ParagraphStyle(name='RightHeading',
               fontSize=10, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name='Signature', fontSize=10,
               alignment=TA_LEFT, spaceBefore=30))
    styles.add(ParagraphStyle(name='TableCell', fontSize=8,
               alignment=TA_LEFT, leading=10, wordWrap='CJK'))
    styles.add(ParagraphStyle(name='TableCellCenter', fontSize=8,
               alignment=TA_CENTER, leading=10))

    # Elementos para el PDF
    elements = []

    # Logo
    logo_path = os.path.join(
        settings.BASE_DIR, "static", "logo", "rbcold-logo.png")
    if os.path.exists(logo_path):
        img = Image(logo_path, width=1.5*inch, height=1.5*inch)
        img.hAlign = 'CENTER'
        elements.append(img)
    else:
        # Si no se encuentra el logo, agregar un texto alternativo
        elements.append(Paragraph("RB COLD CHAIN", styles["CenterTitle"]))

    # Título
    elements.append(Spacer(1, 0.75*inch))
    elements.append(
        Paragraph("GERENCIA DE OPERACIONES", styles["CenterTitle"]))
    elements.append(Paragraph("DOCUMENTO DE SALIDA", styles["CenterTitle"]))
    elements.append(Spacer(1, 0.25*inch))

    # Información de la salida
    # Determinar la fecha - prioridad: exit_date, created_at, N/A
    if exit_instance.exit_date:
        fecha_salida = exit_instance.exit_date.strftime("%d/%m/%Y")
    elif exit_instance.created_at:
        fecha_salida = exit_instance.created_at.strftime("%d/%m/%Y")
    else:
        fecha_salida = "N/A"
    elements.append(
        Paragraph(f"<b>Fecha de Salida:</b> {fecha_salida}", styles["LeftHeading"]))
    elements.append(
        Paragraph(f"<b>Código de Salida:</b> {exit_instance.code}", styles["LeftHeading"]))
    elements.append(
        Paragraph(f"<b>Cliente:</b> {exit_instance.client.name}", styles["LeftHeading"]))

    if hasattr(exit_instance, 'branch') and exit_instance.branch:
        elements.append(
            Paragraph(f"<b>Sucursal:</b> {exit_instance.branch.name}", styles["LeftHeading"]))

    elements.append(Spacer(1, 0.25*inch))

    # Tabla de productos
    data = [["Producto", "SKU", "Cantidad", "Unidad"]]

    # Contar bins únicos y total de items
    unique_bins = set()
    total_items = 0

    print(
        f"DEBUG: Iniciando conteo para la orden de salida {exit_instance.code}")

    # Añadir productos de la orden de salida
    for group in exit_instance.exit_product_groups.all():
        product = group.product
        quantity = group.quantity

        # Obtener SKU del producto
        sku = product.client_sku if product.client_sku else "N/A"

        # Añadir fila a la tabla con Paragraph para permitir wrap
        data.append([
            Paragraph(product.name, styles['TableCell']),
            Paragraph(sku, styles['TableCell']),
            Paragraph(str(quantity), styles['TableCellCenter']),
            Paragraph(product.uom, styles['TableCellCenter'])
        ])

        # Sumar todas las cantidades independientemente del tipo de unidad
        total_items += quantity
        print(f"DEBUG: Producto {product.name} - {quantity} {product.uom}")

        # Obtener los exit_products asociados a este grupo para contar bins únicos
        for exit_product in group.exit_products.all():
            if not exit_product.is_picking:  # Solo bins completos, no picking
                bin = exit_product.bin_product.bin
                bin_id = bin.id
                unique_bins.add(bin_id)
                print(
                    f"DEBUG: Detectado bin_id={bin_id} para producto {product.name}")

    # El total de bins es el número de bins únicos (bins completos)
    total_bins_completos = len(unique_bins)

    # Obtener pallets de picking del exit_detail
    picking_pallets = 0
    try:
        if hasattr(exit_instance, 'exit_detail') and exit_instance.exit_detail:
            picking_pallets = exit_instance.exit_detail.picking_pallets or 0
    except:
        picking_pallets = 0

    # Total de pallets = bins completos + pallets de picking
    total_bins = total_bins_completos + picking_pallets

    print(f"DEBUG: Total de bins completos: {total_bins_completos}")
    print(f"DEBUG: Pallets de picking: {picking_pallets}")
    print(f"DEBUG: Total de pallets/bins: {total_bins}")
    print(f"DEBUG: Total de items: {total_items}")

    # Si hay productos
    if len(data) > 1:
        table = Table(data, colWidths=[
                      2.75*inch, 1.75*inch, 0.75*inch, 0.75*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('TOPPADDING', (0, 1), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('ALIGN', (2, 1), (3, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
        ]))
        elements.append(table)

        print(
            f"DEBUG: TOTALES FINALES: PALLETS/BINS: {total_bins}, ITEMS: {total_items}")
    else:
        elements.append(Paragraph(
            "<i>No hay productos registrados en esta orden de salida.</i>", styles["LeftHeading"]))

    # Resumen de totales
    elements.append(Spacer(1, 0.25*inch))
    elements.append(
        Paragraph(f"<b>Total de PALLETS/BINS:</b> {total_bins}", styles["LeftHeading"]))
    elements.append(
        Paragraph(f"<b>Total de Cajas:</b> {total_items}", styles["LeftHeading"]))

    # Comentarios - temperatura de despacho de la base de datos
    elements.append(Spacer(1, 0.25*inch))
    temperatura_text = "N/A"
    try:
        if hasattr(exit_instance, 'exit_detail') and exit_instance.exit_detail and exit_instance.exit_detail.temperature:
            temperatura_text = f"{exit_instance.exit_detail.temperature} grados Cent."
        else:
            temperatura_text = "____ grados Cent."
    except:
        temperatura_text = "____ grados Cent."

    elements.append(
        Paragraph(f"<b>COMENTARIOS:</b> Temperatura de despacho ( {temperatura_text} )", styles["LeftHeading"]))

    # Responsable (último modificador de la orden)
    responsable_text = "N/A"
    try:
        if exit_instance.updated_by:
            responsable_text = exit_instance.updated_by.get_full_name(
            ) or exit_instance.updated_by.username
    except:
        responsable_text = "N/A"

    elements.append(Spacer(1, 0.15*inch))
    elements.append(
        Paragraph(f"<b>RESPONSABLE:</b> {responsable_text}", styles["LeftHeading"]))

    # Despachado por (operadores que procesaron los productos de salida)
    try:
        operators = set()
        for group in exit_instance.exit_product_groups.all():
            for exit_product in group.exit_products.all():
                if exit_product.is_processed and exit_product.updated_by and exit_product.updated_by.email:
                    operators.add(exit_product.updated_by.email)
        
        if operators:
            operators_text = ", ".join(sorted(operators))
            elements.append(
                Paragraph(f"<b>DESPACHADO POR:</b> {operators_text}", styles["LeftHeading"]))
    except:
        pass

    # Espaciado para firmas
    elements.append(Spacer(1, 0.75*inch))

    # Tabla para firmas
    signature_data = [
        ["____________________", "____________________"],
        ["Entregado por", "Recibido por"],
        ["RB Cold Chain", "Cliente"]
    ]

    signature_table = Table(signature_data, colWidths=[2.5*inch, 2.5*inch])
    signature_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))

    elements.append(signature_table)

    # Pie de página con información de la empresa
    elements.append(Spacer(1, 0.75*inch))
    elements.append(
        Paragraph("Pan América Corporate Center Galera 9095, local 6, Howard Panamá Pacífico, Panamá",
                  styles["LeftHeading"]))
    elements.append(
        Paragraph("<u>www.rbcold.com</u>", styles["LeftHeading"]))

    # Generar PDF
    doc.build(elements)

    # Guardar el PDF en el sistema de archivos
    with open(filepath, 'wb') as file:
        file.write(buffer.getvalue())

    # Retornar la ruta relativa para guardar en la BD
    return relative_path


def update_exit_pdf(exit_instance):
    """
    Actualiza o crea el PDF de una orden de salida y actualiza el campo en la BD.

    Args:
        exit_instance: Instancia del modelo Exit
    """
    # Intentar generar el PDF con reintentos
    max_retries = 3
    retry_delay = 1  # segundos

    for attempt in range(max_retries):
        # Generar el PDF
        pdf_path = generate_exit_pdf(exit_instance)

        # Verificar si la orden tiene productos
        groups_count = exit_instance.exit_product_groups.count()

        if groups_count > 0 or attempt == max_retries - 1:
            # Si tiene productos o es el último intento, usar este PDF
            break

        print(
            f"Intento {attempt+1}/{max_retries}: La orden no tiene productos aún. Esperando {retry_delay} segundos...")
        time.sleep(retry_delay)
        retry_delay *= 2  # Duplicar el tiempo de espera en cada intento

    # Actualizar campo en la base de datos si existe
    if hasattr(exit_instance, 'exit_detail'):
        if not hasattr(exit_instance.exit_detail, 'pdf_document'):
            # El campo no existe, verificar si hay que crearlo
            from django.db import connection
            cursor = connection.cursor()
            try:
                cursor.execute("""
                    ALTER TABLE order_exitdetail 
                    ADD COLUMN IF NOT EXISTS pdf_document VARCHAR(255) NULL;
                """)
            except Exception as e:
                print(f"Error al verificar campo pdf_document: {e}")

        exit_instance.exit_detail.pdf_document = pdf_path
        exit_instance.exit_detail.save(update_fields=['pdf_document'])

    return pdf_path


def generate_entry_pdf(entry_instance):
    """
    Genera un PDF para la orden de entrada con la información requerida.

    Args:
        entry_instance: Instancia del modelo Entry

    Returns:
        str: Ruta relativa al archivo PDF generado
    """
    # Forzar sincronización con la base de datos
    connection.ensure_connection()

    # Recargar la instancia para asegurarnos de tener datos actualizados
    entry_instance.refresh_from_db()

    # Pre-cargar las relaciones para evitar consultas N+1
    prefetch_related_objects([entry_instance],
                             'entry_products',
                             'entry_products__product')

    # Importar Bin para contar bins
    from apps.inventory.models import Bin

    # Verificar que la instancia tiene productos
    products_count = entry_instance.entry_products.count()
    print(
        f"DEBUG: La orden de entrada {entry_instance.code} tiene {products_count} productos")

    if products_count == 0:
        print(
            f"ADVERTENCIA: La orden de entrada {entry_instance.code} no tiene productos. Puede que el PDF se genere vacío.")

    relative_path = build_wms_entry_pdf_relative(entry_instance)
    filepath = ensure_media_parent_dir(relative_path)
    filename = os.path.basename(relative_path)

    # Configuración del documento
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=0.75*inch,
        bottomMargin=0.75*inch,
        leftMargin=0.75*inch,
        rightMargin=0.75*inch
    )

    # Estilos para el documento
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='CenterTitle', fontSize=16,
               alignment=TA_CENTER, spaceAfter=12))
    styles.add(ParagraphStyle(name='LeftHeading',
               fontSize=12, alignment=TA_LEFT, spaceBefore=6))
    styles.add(ParagraphStyle(name='RightHeading',
               fontSize=10, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name='Signature', fontSize=10,
               alignment=TA_LEFT, spaceBefore=30))
    styles.add(ParagraphStyle(name='TableCell', fontSize=8,
               alignment=TA_LEFT, leading=10, wordWrap='CJK'))
    styles.add(ParagraphStyle(name='TableCellCenter', fontSize=8,
               alignment=TA_CENTER, leading=10))

    # Elementos para el PDF
    elements = []

    # Logo
    logo_path = os.path.join(
        settings.BASE_DIR, "static", "logo", "rbcold-logo.png")
    if os.path.exists(logo_path):
        img = Image(logo_path, width=1.5*inch, height=1.5*inch)
        img.hAlign = 'CENTER'
        elements.append(img)
    else:
        # Si no se encuentra el logo, agregar un texto alternativo
        elements.append(Paragraph("RB COLD CHAIN", styles["CenterTitle"]))

    # Título
    elements.append(Spacer(1, 0.75*inch))
    elements.append(
        Paragraph("GERENCIA DE OPERACIONES", styles["CenterTitle"]))
    elements.append(Paragraph("DOCUMENTO DE ENTRADA", styles["CenterTitle"]))
    elements.append(Spacer(1, 0.25*inch))

    # Información de la entrada
    # Determinar la fecha - prioridad: entry_date, created_at, N/A
    if entry_instance.entry_date:
        fecha_entrada = entry_instance.entry_date.strftime("%d/%m/%Y")
    elif entry_instance.created_at:
        fecha_entrada = entry_instance.created_at.strftime("%d/%m/%Y")
    else:
        fecha_entrada = "N/A"
    elements.append(
        Paragraph(f"<b>Fecha de Entrada:</b> {fecha_entrada}", styles["LeftHeading"]))
    elements.append(
        Paragraph(f"<b>Código de Entrada:</b> {entry_instance.code}", styles["LeftHeading"]))
    elements.append(
        Paragraph(f"<b>Cliente:</b> {entry_instance.client.name}", styles["LeftHeading"]))

    if entry_instance.storage:
        elements.append(
            Paragraph(f"<b>Almacén:</b> {entry_instance.storage.name}", styles["LeftHeading"]))

    if entry_instance.condition:
        elements.append(
            Paragraph(f"<b>Condición:</b> {entry_instance.get_condition_display()}", styles["LeftHeading"]))

    if entry_instance.in_type:
        elements.append(
            Paragraph(f"<b>Tipo de Entrada:</b> {entry_instance.get_in_type_display()}", styles["LeftHeading"]))

    elements.append(Spacer(1, 0.25*inch))

    # Tabla de productos
    data = [["Producto", "SKU", "Cantidad", "Unidad", "Lote", "Fecha Exp."]]

    total_items = 0
    # Contar bins únicos asociados a esta entrada
    unique_bins = set()

    # Obtener todos los bins asociados a esta entrada
    bins_associated = Bin.objects.filter(entry=entry_instance)
    for bin in bins_associated:
        unique_bins.add(bin.id)

    total_bins = len(unique_bins)

    print(
        f"DEBUG: Iniciando conteo para la orden de entrada {entry_instance.code}")
    print(f"DEBUG: Total de bins asociados: {total_bins}")

    # Añadir productos de la orden de entrada
    for entry_product in entry_instance.entry_products.all():
        product = entry_product.product
        real_quantity = entry_product.real_quantity

        # Obtener SKU del producto
        sku = product.client_sku if product.client_sku else "N/A"

        # Formatear fecha de expiración
        exp_date = entry_product.expiration_date.strftime(
            "%d/%m/%Y") if entry_product.expiration_date else "N/A"

        # Formatear lote
        batch = entry_product.batch or "N/A"

        # Añadir fila a la tabla con Paragraph para permitir wrap
        data.append([
            Paragraph(product.name, styles['TableCell']),
            Paragraph(sku, styles['TableCell']),
            Paragraph(str(real_quantity), styles['TableCellCenter']),
            Paragraph(product.uom, styles['TableCellCenter']),
            Paragraph(batch, styles['TableCell']),
            Paragraph(exp_date, styles['TableCellCenter'])
        ])

        # Sumar todas las cantidades reales
        total_items += real_quantity
        print(
            f"DEBUG: Producto {product.name} - {real_quantity} {product.uom}")

    print(f"DEBUG: Total de items: {total_items}")

    # Si hay productos
    if len(data) > 1:
        table = Table(data, colWidths=[
                      2.5*inch, 1.5*inch, 0.75*inch, 0.75*inch, 1*inch, 0.75*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('TOPPADDING', (0, 1), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('ALIGN', (2, 1), (5, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
        ]))
        elements.append(table)

        print(
            f"DEBUG: TOTALES FINALES: CANTIDAD: {total_items}")
    else:
        elements.append(Paragraph(
            "<i>No hay productos registrados en esta orden de entrada.</i>", styles["LeftHeading"]))

    # Resumen de totales
    elements.append(Spacer(1, 0.25*inch))
    elements.append(
        Paragraph(f"<b>Total de PALLETS/BINS:</b> {total_bins}", styles["LeftHeading"]))
    elements.append(
        Paragraph(f"<b>Total de Cajas:</b> {total_items}", styles["LeftHeading"]))

    # Comentarios - temperatura de llegada de la base de datos
    elements.append(Spacer(1, 0.25*inch))
    temperatura_text = "N/A"
    try:
        if hasattr(entry_instance, 'entry_detail') and entry_instance.entry_detail and entry_instance.entry_detail.arrival_temperature:
                temperatura_text = f"{entry_instance.entry_detail.arrival_temperature} grados Cent."
        else:
            temperatura_text = "____ grados Cent."
    except:
        temperatura_text = "____ grados Cent."

    elements.append(
        Paragraph(f"<b>COMENTARIOS:</b> Temperatura de llegada ( {temperatura_text} )", styles["LeftHeading"]))

    # Descripción si existe
    try:
        if hasattr(entry_instance, 'entry_detail') and entry_instance.entry_detail and entry_instance.entry_detail.description:
            elements.append(
                Paragraph(entry_instance.entry_detail.description, styles["LeftHeading"]))
    except:
        pass

    # Responsable (último modificador de la orden)
    responsable_text = "N/A"
    try:
        if entry_instance.updated_by:
            responsable_text = entry_instance.updated_by.get_full_name(
            ) or entry_instance.updated_by.username
    except:
        responsable_text = "N/A"

    elements.append(Spacer(1, 0.15*inch))
    elements.append(
        Paragraph(f"<b>RESPONSABLE:</b> {responsable_text}", styles["LeftHeading"]))

    # Almacenado por (operadores que procesaron los bins)
    try:
        operators = set()
        for bin in entry_instance.bins.all():
            if bin.updated_by and bin.updated_by.email:
                operators.add(bin.updated_by.email)
            elif bin.created_by and bin.created_by.email:
                operators.add(bin.created_by.email)
        
        if operators:
            operators_text = ", ".join(sorted(operators))
            elements.append(
                Paragraph(f"<b>ALMACENADO POR:</b> {operators_text}", styles["LeftHeading"]))
    except:
        pass

    # Espaciado para firmas
    elements.append(Spacer(1, 0.75*inch))

    # Tabla para firmas
    signature_data = [
        ["____________________", "____________________"],
        ["Recibido por", "Entregado por"],
        ["RB Cold Chain", "Cliente"]
    ]

    signature_table = Table(signature_data, colWidths=[2.5*inch, 2.5*inch])
    signature_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))

    elements.append(signature_table)

    # Pie de página con información de la empresa
    elements.append(Spacer(1, 0.75*inch))
    elements.append(
        Paragraph("Pan América Corporate Center Galera 9095, local 6, Howard Panamá Pacífico, Panamá",
                  styles["LeftHeading"]))
    elements.append(
        Paragraph("<u>www.rbcold.com</u>", styles["LeftHeading"]))

    # Generar PDF
    doc.build(elements)

    # Guardar el PDF en el sistema de archivos
    with open(filepath, 'wb') as file:
        file.write(buffer.getvalue())

    # Retornar la ruta relativa para guardar en la BD
    return relative_path


def update_entry_pdf(entry_instance):
    """
    Actualiza o crea el PDF de una orden de entrada y actualiza el campo en la BD.

    Args:
        entry_instance: Instancia del modelo Entry

    Returns:
        str: Ruta del archivo PDF generado
    """
    # Intentar generar el PDF con reintentos
    max_retries = 3
    retry_delay = 1  # segundos

    for attempt in range(max_retries):
        # Generar el PDF
        pdf_path = generate_entry_pdf(entry_instance)

        # Verificar si la orden tiene productos
        products_count = entry_instance.entry_products.count()

        if products_count > 0 or attempt == max_retries - 1:
            # Si tiene productos o es el último intento, usar este PDF
            break

        print(
            f"Intento {attempt+1}/{max_retries}: La orden no tiene productos aún. Esperando {retry_delay} segundos...")
        time.sleep(retry_delay)
        retry_delay *= 2  # Duplicar el tiempo de espera en cada intento

    # Actualizar campo en la base de datos si existe
    if hasattr(entry_instance, 'entry_detail'):
        if not hasattr(entry_instance.entry_detail, 'pdf_document'):
            # El campo no existe, verificar si hay que crearlo
            from django.db import connection
            cursor = connection.cursor()
            try:
                cursor.execute("""
                    ALTER TABLE order_entrydetail 
                    ADD COLUMN IF NOT EXISTS pdf_document VARCHAR(255) NULL;
                """)
            except Exception as e:
                print(f"Error al verificar campo pdf_document: {e}")

        entry_instance.entry_detail.pdf_document = pdf_path
        entry_instance.entry_detail.save(update_fields=['pdf_document'])

    return pdf_path


def export_entry_excel(queryset):
    """
    Exporta órdenes de entrada a Excel.
    Cada producto de una orden genera una fila separada.

    Args:
        queryset: QuerySet de Entry

    Returns:
        BytesIO: Buffer con el contenido del archivo Excel
    """
    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Entradas')

    # Styles
    header_style = xlwt.XFStyle()
    header_font = xlwt.Font()
    header_font.bold = True
    header_font.colour_index = xlwt.Style.colour_map['white']
    header_style.font = header_font

    header_pattern = xlwt.Pattern()
    header_pattern.pattern = xlwt.Pattern.SOLID_PATTERN
    header_pattern.pattern_fore_colour = xlwt.Style.colour_map['dark_blue']
    header_style.pattern = header_pattern

    header_alignment = xlwt.Alignment()
    header_alignment.horz = xlwt.Alignment.HORZ_CENTER
    header_alignment.vert = xlwt.Alignment.VERT_CENTER
    header_style.alignment = header_alignment

    data_style = xlwt.XFStyle()
    data_alignment = xlwt.Alignment()
    data_alignment.wrap = 1
    data_alignment.vert = xlwt.Alignment.VERT_TOP
    data_style.alignment = data_alignment

    # Estilo para números (cantidad)
    number_style = xlwt.XFStyle()
    number_alignment = xlwt.Alignment()
    number_alignment.horz = xlwt.Alignment.HORZ_RIGHT
    number_alignment.vert = xlwt.Alignment.VERT_TOP
    number_style.alignment = number_alignment

    # Headers - 14 columnas ahora
    columns = [
        'Fecha de Creación',
        'Código',
        'Cliente',
        'Almacén',
        'Condición',
        'Tipo de Entrada',
        'Fecha de Entrada',
        'Estado',
        'Producto',
        'Cantidad Reportada',
        'Cantidad Real',
        'UOM',
        'Total Restibas',
        'Total Wraps'
    ]

    row_num = 0
    for col_num, column_title in enumerate(columns):
        ws.write(row_num, col_num, column_title, header_style)
        ws.row(row_num).height_mismatch = True
        ws.row(row_num).height = 400

    # Data rows - una fila por cada producto de cada orden
    for entry in queryset:
        # Fechas
        created_date_str = entry.created_at.strftime(
            '%d/%m/%Y %H:%M') if entry.created_at else "N/A"
        entry_date_str = entry.entry_date.strftime(
            '%d/%m/%Y') if entry.entry_date else "N/A"

        # Datos comunes de la orden
        client_name = entry.client.name if entry.client else "N/A"
        storage_name = entry.storage.name if entry.storage else "N/A"
        condition = entry.get_condition_display() if entry.condition else "N/A"
        in_type = entry.get_in_type_display() if entry.in_type else "N/A"
        status = entry.get_status_display()
        total_restibas = entry.total_restibas or 0
        total_wraps = entry.total_wraps or 0

        # Obtener productos de la orden
        entry_products = entry.entry_products.all()

        if entry_products.exists():
            # Una fila por cada producto
            for ep in entry_products:
                row_num += 1
                product_name = ep.product.name if ep.product else "N/A"
                quantity = ep.quantity or 0
                real_quantity = ep.real_quantity or 0
                uom = ep.product.uom if ep.product else "N/A"

                ws.write(row_num, 0, created_date_str, data_style)
                ws.write(row_num, 1, entry.code, data_style)
                ws.write(row_num, 2, client_name, data_style)
                ws.write(row_num, 3, storage_name, data_style)
                ws.write(row_num, 4, condition, data_style)
                ws.write(row_num, 5, in_type, data_style)
                ws.write(row_num, 6, entry_date_str, data_style)
                ws.write(row_num, 7, status, data_style)
                ws.write(row_num, 8, product_name, data_style)
                ws.write(row_num, 9, quantity, number_style)
                ws.write(row_num, 10, real_quantity, number_style)
                ws.write(row_num, 11, uom, data_style)
                ws.write(row_num, 12, total_restibas, number_style)
                ws.write(row_num, 13, total_wraps, number_style)
        else:
            # Si no hay productos, crear una fila con "Sin productos"
            row_num += 1
            ws.write(row_num, 0, created_date_str, data_style)
            ws.write(row_num, 1, entry.code, data_style)
            ws.write(row_num, 2, client_name, data_style)
            ws.write(row_num, 3, storage_name, data_style)
            ws.write(row_num, 4, condition, data_style)
            ws.write(row_num, 5, in_type, data_style)
            ws.write(row_num, 6, entry_date_str, data_style)
            ws.write(row_num, 7, status, data_style)
            ws.write(row_num, 8, "Sin productos", data_style)
            ws.write(row_num, 9, 0, number_style)
            ws.write(row_num, 10, 0, number_style)
            ws.write(row_num, 11, "N/A", data_style)
            ws.write(row_num, 12, total_restibas, number_style)
            ws.write(row_num, 13, total_wraps, number_style)

    # Adjust column widths
    column_widths = [20, 30, 25, 20, 15, 15, 15, 12, 40, 18, 15, 12, 15, 12]
    for col_num, width in enumerate(column_widths):
        ws.col(col_num).width = 256 * width

    # Save to buffer
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return output


def export_exit_excel(queryset):
    """
    Exporta órdenes de salida a Excel.
    Cada producto de una orden genera una fila separada.

    Args:
        queryset: QuerySet de Exit

    Returns:
        BytesIO: Buffer con el contenido del archivo Excel
    """
    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Salidas')

    # Styles
    header_style = xlwt.XFStyle()
    header_font = xlwt.Font()
    header_font.bold = True
    header_font.colour_index = xlwt.Style.colour_map['white']
    header_style.font = header_font

    header_pattern = xlwt.Pattern()
    header_pattern.pattern = xlwt.Pattern.SOLID_PATTERN
    header_pattern.pattern_fore_colour = xlwt.Style.colour_map['dark_blue']
    header_style.pattern = header_pattern

    header_alignment = xlwt.Alignment()
    header_alignment.horz = xlwt.Alignment.HORZ_CENTER
    header_alignment.vert = xlwt.Alignment.VERT_CENTER
    header_style.alignment = header_alignment

    data_style = xlwt.XFStyle()
    data_alignment = xlwt.Alignment()
    data_alignment.wrap = 1
    data_alignment.vert = xlwt.Alignment.VERT_TOP
    data_style.alignment = data_alignment

    # Estilo para números (cantidad)
    number_style = xlwt.XFStyle()
    number_alignment = xlwt.Alignment()
    number_alignment.horz = xlwt.Alignment.HORZ_RIGHT
    number_alignment.vert = xlwt.Alignment.VERT_TOP
    number_style.alignment = number_alignment

    # Headers - 11 columnas ahora
    columns = [
        'Fecha de Creación',
        'Código',
        'Cliente',
        'Sucursal',
        'Fecha de Salida',
        'Estado',
        'Producto',
        'Condición',
        'Cantidad',
        'UOM',
        'Pallets de Picking'
    ]

    row_num = 0
    for col_num, column_title in enumerate(columns):
        ws.write(row_num, col_num, column_title, header_style)
        ws.row(row_num).height_mismatch = True
        ws.row(row_num).height = 400

    # Data rows - una fila por cada producto de cada orden
    for exit in queryset:
        # Fechas
        created_date_str = exit.created_at.strftime(
            '%d/%m/%Y %H:%M') if exit.created_at else "N/A"
        exit_date_str = exit.exit_date.strftime(
            '%d/%m/%Y') if exit.exit_date else "N/A"

        # Pallets de picking
        picking_pallets = 0
        try:
            if hasattr(exit, 'exit_detail') and exit.exit_detail:
                picking_pallets = exit.exit_detail.picking_pallets or 0
        except:
            picking_pallets = 0

        # Datos comunes de la orden
        client_name = exit.client.name if exit.client else "N/A"
        branch_name = exit.branch.name if hasattr(
            exit, 'branch') and exit.branch else "N/A"
        status = exit.get_status_display()

        # Obtener productos de la orden
        product_groups = exit.exit_product_groups.all()

        if product_groups.exists():
            # Una fila por cada producto
            for group in product_groups:
                row_num += 1
                product_name = group.product.name if group.product else "N/A"
                product_condition = group.product.get_condition_display() if group.product and group.product.condition else "N/A"
                quantity = group.quantity
                uom = group.product.uom if group.product else "N/A"

                ws.write(row_num, 0, created_date_str, data_style)
                ws.write(row_num, 1, exit.code, data_style)
                ws.write(row_num, 2, client_name, data_style)
                ws.write(row_num, 3, branch_name, data_style)
                ws.write(row_num, 4, exit_date_str, data_style)
                ws.write(row_num, 5, status, data_style)
                ws.write(row_num, 6, product_name, data_style)
                ws.write(row_num, 7, product_condition, data_style)
                ws.write(row_num, 8, quantity, number_style)
                ws.write(row_num, 9, uom, data_style)
                ws.write(row_num, 10, picking_pallets, number_style)
        else:
            # Si no hay productos, crear una fila con "Sin productos"
            row_num += 1
            ws.write(row_num, 0, created_date_str, data_style)
            ws.write(row_num, 1, exit.code, data_style)
            ws.write(row_num, 2, client_name, data_style)
            ws.write(row_num, 3, branch_name, data_style)
            ws.write(row_num, 4, exit_date_str, data_style)
            ws.write(row_num, 5, status, data_style)
            ws.write(row_num, 6, "Sin productos", data_style)
            ws.write(row_num, 7, "N/A", data_style)
            ws.write(row_num, 8, 0, number_style)
            ws.write(row_num, 9, "N/A", data_style)
            ws.write(row_num, 10, picking_pallets, number_style)

    # Adjust column widths (11 columnas)
    column_widths = [20, 30, 25, 20, 15, 12, 40, 15, 12, 12, 18]
    for col_num, width in enumerate(column_widths):
        ws.col(col_num).width = 256 * width

    # Save to buffer
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return output


def validate_exit_import(file, user=None):
    """
    Valida un archivo Excel para importación de órdenes de salida.

    Args:
        file: Archivo Excel subido
        user: Usuario que realiza la operación

    Returns:
        dict: Resultado de la validación con errores o datos procesados
    """
    import openpyxl
    from datetime import datetime, date
    from apps.core.models import Client, Branch
    from apps.inventory.models import Product

    errors = []
    warnings = []
    rows_data = []

    try:
        # Cargar el archivo Excel
        try:
            wb = openpyxl.load_workbook(file, data_only=True)
        except Exception as load_error:
            error_msg = str(load_error)
            if 'not a zip file' in error_msg.lower() or 'xlrd' in error_msg.lower():
                return {
                    'valid': False,
                    'errors': [
                        "El archivo parece ser un Excel antiguo (.xls). Por favor, guárdelo como Excel (.xlsx) y vuelva a intentar.",
                        f"Detalle técnico: {error_msg}"
                    ],
                    'warnings': [],
                    'summary': None,
                    'data': None
                }
            elif 'corrupt' in error_msg.lower() or 'invalid' in error_msg.lower():
                return {
                    'valid': False,
                    'errors': [
                        "El archivo Excel parece estar corrupto o no es válido.",
                        f"Detalle técnico: {error_msg}"
                    ],
                    'warnings': [],
                    'summary': None,
                    'data': None
                }
            else:
                return {
                    'valid': False,
                    'errors': [
                        f"No se pudo abrir el archivo Excel: {error_msg}"
                    ],
                    'warnings': [],
                    'summary': None,
                    'data': None
                }
        ws = wb.active

        # Obtener headers (primera fila)
        headers = []
        for cell in ws[1]:
            headers.append(str(cell.value).strip().upper()
                           if cell.value else "")

        # Mapear columnas esperadas
        expected_columns = {
            'CLIENTE': None,
            'SUCURSAL': None,
            'CODIGO SKU': None,
            'NOMBRE PRODUCTO': None,
            'CANTIDAD': None,
            'PRESENTACION': None,
            'DESCRIPCION': None,
        }

        # Buscar columnas por nombre (flexible)
        for idx, header in enumerate(headers):
            if 'CLIENTE' in header:
                expected_columns['CLIENTE'] = idx
            elif 'SUCURSAL' in header:
                expected_columns['SUCURSAL'] = idx
            elif 'SKU' in header or 'CODIGO' in header:
                expected_columns['CODIGO SKU'] = idx
            elif 'PRODUCTO' in header and 'NOMBRE' in header:
                expected_columns['NOMBRE PRODUCTO'] = idx
            elif 'CANTIDAD' in header:
                expected_columns['CANTIDAD'] = idx
            elif 'PRESENTACION' in header or 'UOM' in header:
                expected_columns['PRESENTACION'] = idx
            elif 'DESCRIPCION' in header or 'FECHA' in header:
                expected_columns['DESCRIPCION'] = idx

        # Verificar columnas obligatorias
        required_cols = ['CLIENTE', 'SUCURSAL', 'CODIGO SKU', 'CANTIDAD']
        missing_cols = [
            col for col in required_cols if expected_columns.get(col) is None]
        if missing_cols:
            return {
                'valid': False,
                'errors': [f"Columnas faltantes en el Excel: {', '.join(missing_cols)}"],
                'warnings': [],
                'summary': None,
                'data': None
            }

        # Procesar filas de datos (desde la fila 2)
        today = date.today()
        client_cache = {}
        branch_cache = {}
        product_cache = {}

        for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            # Verificar si la fila está vacía
            if not any(row):
                continue

            row_errors = []

            # Extraer valores
            client_name = str(row[expected_columns['CLIENTE']]).strip(
            ) if row[expected_columns['CLIENTE']] else None
            branch_name = str(row[expected_columns['SUCURSAL']]).strip(
            ) if row[expected_columns['SUCURSAL']] else None
            sku = str(row[expected_columns['CODIGO SKU']]).strip(
            ) if row[expected_columns['CODIGO SKU']] else None
            quantity = row[expected_columns['CANTIDAD']]
            description = str(row[expected_columns['DESCRIPCION']]).strip(
            ) if expected_columns['DESCRIPCION'] is not None and row[expected_columns['DESCRIPCION']] else None

            # Validar cliente
            if not client_name:
                row_errors.append("Cliente es obligatorio")
            else:
                if client_name not in client_cache:
                    client = Client.objects.filter(
                        name__iexact=client_name).first()
                    client_cache[client_name] = client

                if not client_cache[client_name]:
                    row_errors.append(f"Cliente '{client_name}' no encontrado")

            # Validar sucursal
            if not branch_name:
                row_errors.append("Sucursal es obligatoria")
            else:
                cache_key = f"{client_name}_{branch_name}"
                if cache_key not in branch_cache:
                    client = client_cache.get(client_name)
                    if client:
                        branch = Branch.objects.filter(
                            client=client,
                            name__iexact=branch_name
                        ).first()
                        branch_cache[cache_key] = branch
                    else:
                        branch_cache[cache_key] = None

                if client_cache.get(client_name) and not branch_cache.get(cache_key):
                    row_errors.append(
                        f"Sucursal '{branch_name}' no encontrada para el cliente '{client_name}'")

            # Validar SKU
            if not sku:
                row_errors.append("SKU es obligatorio")
            else:
                if sku not in product_cache:
                    client = client_cache.get(client_name)
                    if client:
                        product = Product.objects.filter(
                            client=client,
                            client_sku=sku
                        ).first()
                        product_cache[sku] = product
                    else:
                        product_cache[sku] = None

                if client_cache.get(client_name) and not product_cache.get(sku):
                    row_errors.append(
                        f"SKU '{sku}' no encontrado para el cliente '{client_name}'")

            # Validar cantidad
            if quantity is None:
                row_errors.append("Cantidad es obligatoria")
            else:
                try:
                    quantity = int(quantity)
                    if quantity <= 0:
                        row_errors.append("Cantidad debe ser mayor a 0")
                except (ValueError, TypeError):
                    row_errors.append(f"Cantidad inválida: '{quantity}'")
                    quantity = 0

            # Extraer fecha de descripción si existe (formato: "DESPACHO 27/11/2025")
            exit_date = None
            if description:
                import re
                date_match = re.search(
                    r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', description)
                if date_match:
                    try:
                        day, month, year = date_match.groups()
                        exit_date = date(int(year), int(month), int(day))
                        if exit_date < today:
                            warnings.append(
                                f"Fila {row_num}: Fecha de salida {exit_date.strftime('%d/%m/%Y')} es anterior a hoy")
                    except ValueError:
                        row_errors.append(
                            f"Fecha inválida en descripción: '{description}'")

            # Si hay errores en la fila, agregarlos
            if row_errors:
                for error in row_errors:
                    errors.append(f"Fila {row_num}: {error}")
            else:
                # Agregar datos válidos
                rows_data.append({
                    'row_num': row_num,
                    'client_name': client_name,
                    'client': client_cache.get(client_name),
                    'branch_name': branch_name,
                    'branch': branch_cache.get(f"{client_name}_{branch_name}"),
                    'sku': sku,
                    'product': product_cache.get(sku),
                    'quantity': quantity,
                    'exit_date': exit_date,
                    'description': description
                })

        # Si no hay filas válidas
        if not rows_data and not errors:
            errors.append("El archivo no contiene datos válidos")

        # Validar stock disponible para cada producto
        stock_errors = []
        if rows_data and not errors:
            from apps.inventory.models import BinProduct
            from apps.core.choices import BinStatus
            from django.db.models import Sum

            # Agrupar cantidades solicitadas por cliente + producto + sucursal (para el mensaje)
            requested_quantities = {}
            for row in rows_data:
                client_id = row['client'].id
                product_id = row['product'].id
                key = f"{client_id}_{product_id}"
                if key not in requested_quantities:
                    requested_quantities[key] = {
                        'client': row['client'],
                        'product': row['product'],
                        'sku': row['sku'],
                        'branch': row.get('branch'),  # Incluir sucursal para el mensaje
                        'total_requested': 0
                    }
                requested_quantities[key]['total_requested'] += row['quantity']

            # Verificar stock disponible para cada producto
            for key, data in requested_quantities.items():
                client = data['client']
                product = data['product']
                total_requested = data['total_requested']
                branch = data.get('branch')
                branch_name = branch.name if branch else 'Sin sucursal'

                # Calcular stock disponible (bins almacenados)
                available_stock = BinProduct.objects.filter(
                    bin__client=client,
                    entry_product__product=product,
                    bin__status=BinStatus.ALMACENADO,
                    quantity__gt=0
                ).aggregate(total=Sum('quantity'))['total'] or 0

                # Calcular reservas pendientes (salidas pendientes y en progreso)
                from apps.order.models import ExitProduct
                reserved = ExitProduct.objects.filter(
                    exit_product_group__exit__client=client,
                    exit_product_group__product=product,
                    exit_product_group__exit__status__in=[
                        'pendiente', 'en_progreso'],
                    is_processed=False
                ).aggregate(total=Sum('quantity'))['total'] or 0

                # Stock real disponible
                real_available = available_stock - reserved

                if total_requested > real_available:
                    if real_available <= 0:
                        # No hay inventario disponible
                        stock_errors.append(
                            f"No hay inventario SKU {data['sku']} para la sucursal {branch_name}"
                        )
                    else:
                        # Hay inventario pero no suficiente
                        stock_errors.append(
                            f"SKU '{data['sku']}' para sucursal {branch_name}: Solicitado {total_requested}, "
                            f"Disponible {real_available} (Stock: {available_stock}, Reservado: {reserved})"
                        )

            if stock_errors:
                errors.extend(stock_errors)

        # Crear resumen de órdenes a crear
        summary = None
        if rows_data and not errors:
            # Agrupar por cliente + sucursal
            orders_to_create = {}
            for row in rows_data:
                key = f"{row['client'].id}_{row['branch'].id if row['branch'] else 'no_branch'}"
                if key not in orders_to_create:
                    orders_to_create[key] = {
                        'client': row['client'],
                        'branch': row['branch'],
                        'exit_date': row['exit_date'],
                        'products': []
                    }
                orders_to_create[key]['products'].append({
                    'product': row['product'],
                    'quantity': row['quantity'],
                    'sku': row['sku']
                })
                # Usar la primera fecha encontrada para el grupo
                if not orders_to_create[key]['exit_date'] and row['exit_date']:
                    orders_to_create[key]['exit_date'] = row['exit_date']

            summary = {
                'total_rows': len(rows_data),  # Total de filas en el Excel
                # Total de órdenes a crear
                'total_orders': len(orders_to_create),
                # Total de productos (igual a filas)
                'total_products': len(rows_data),
                'orders': [
                    {
                        'client': order['client'].name,
                        'branch': order['branch'].name if order['branch'] else 'Sin sucursal',
                        'exit_date': order['exit_date'].strftime('%d/%m/%Y') if order['exit_date'] else 'Sin fecha',
                        'products_count': len(order['products']),
                        'products': [
                            {'sku': p['sku'], 'name': p['product'].name,
                                'quantity': p['quantity']}
                            for p in order['products']
                        ]
                    }
                    for order in orders_to_create.values()
                ]
            }

        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'summary': summary
            # 'data' no se incluye en la respuesta JSON porque contiene objetos Django no serializables
            # La función import_exits_from_excel vuelve a validar el archivo
        }

    except Exception as e:
        return {
            'valid': False,
            'errors': [f"Error al procesar el archivo: {str(e)}"],
            'warnings': [],
            'summary': None
        }


@transaction.atomic
def import_exits_from_excel(file, user=None, exit_date_override=None):
    """
    Importa órdenes de salida desde un archivo Excel validado.

    Args:
        file: Archivo Excel subido
        user: Usuario que realiza la operación
        exit_date_override: Fecha de salida a usar para todas las órdenes (opcional)

    Returns:
        dict: Resultado de la importación
    """
    import openpyxl
    from datetime import date
    from apps.core.models import Client, Branch
    from apps.inventory.models import Product

    rows_data = []

    try:
        # Cargar el archivo Excel
        file.seek(0)  # Asegurar que estamos al inicio del archivo
        wb = openpyxl.load_workbook(file, data_only=True)
        ws = wb.active

        # Obtener headers
        headers = []
        for cell in ws[1]:
            headers.append(str(cell.value).strip().upper()
                           if cell.value else "")

        # Mapear columnas
        expected_columns = {
            'CLIENTE': None, 'SUCURSAL': None, 'CODIGO SKU': None,
            'CANTIDAD': None, 'DESCRIPCION': None
        }
        for idx, header in enumerate(headers):
            if 'CLIENTE' in header:
                expected_columns['CLIENTE'] = idx
            elif 'SUCURSAL' in header:
                expected_columns['SUCURSAL'] = idx
            elif 'SKU' in header or 'CODIGO' in header:
                expected_columns['CODIGO SKU'] = idx
            elif 'CANTIDAD' in header:
                expected_columns['CANTIDAD'] = idx
            elif 'DESCRIPCION' in header or 'FECHA' in header:
                expected_columns['DESCRIPCION'] = idx

        # Verificar columnas obligatorias
        required_cols = ['CLIENTE', 'SUCURSAL', 'CODIGO SKU', 'CANTIDAD']
        missing_cols = [
            col for col in required_cols if expected_columns.get(col) is None]
        if missing_cols:
            return {
                'success': False,
                'message': 'El archivo tiene errores',
                'errors': [f"Columnas faltantes: {', '.join(missing_cols)}"],
                'created_orders': []
            }

        # Procesar filas
        today = date.today()
        client_cache = {}
        branch_cache = {}
        product_cache = {}
        errors = []

        for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(row):
                continue

            client_name = str(row[expected_columns['CLIENTE']]).strip(
            ) if row[expected_columns['CLIENTE']] else None
            branch_name = str(row[expected_columns['SUCURSAL']]).strip(
            ) if row[expected_columns['SUCURSAL']] else None
            sku = str(row[expected_columns['CODIGO SKU']]).strip(
            ) if row[expected_columns['CODIGO SKU']] else None
            quantity = row[expected_columns['CANTIDAD']]
            description = str(row[expected_columns['DESCRIPCION']]).strip(
            ) if expected_columns['DESCRIPCION'] is not None and row[expected_columns['DESCRIPCION']] else None

            # Validar y obtener objetos
            if not client_name:
                errors.append(f"Fila {row_num}: Cliente es obligatorio")
                continue
            if client_name not in client_cache:
                client_cache[client_name] = Client.objects.filter(
                    name__iexact=client_name).first()
            if not client_cache[client_name]:
                errors.append(
                    f"Fila {row_num}: Cliente '{client_name}' no encontrado")
                continue

            if not branch_name:
                errors.append(f"Fila {row_num}: Sucursal es obligatoria")
                continue
            cache_key = f"{client_name}_{branch_name}"
            if cache_key not in branch_cache:
                branch_cache[cache_key] = Branch.objects.filter(
                    client=client_cache[client_name], name__iexact=branch_name).first()
            if not branch_cache[cache_key]:
                errors.append(
                    f"Fila {row_num}: Sucursal '{branch_name}' no encontrada")
                continue

            if not sku:
                errors.append(f"Fila {row_num}: SKU es obligatorio")
                continue
            if sku not in product_cache:
                product_cache[sku] = Product.objects.filter(
                    client=client_cache[client_name], client_sku=sku).first()
            if not product_cache[sku]:
                errors.append(f"Fila {row_num}: SKU '{sku}' no encontrado")
                continue

            try:
                quantity = int(quantity)
                if quantity <= 0:
                    errors.append(
                        f"Fila {row_num}: Cantidad debe ser mayor a 0")
                    continue
            except (ValueError, TypeError):
                errors.append(f"Fila {row_num}: Cantidad inválida")
                continue

            # Extraer fecha
            import re
            exit_date = None
            if description:
                date_match = re.search(
                    r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', description)
                if date_match:
                    try:
                        day, month, year = date_match.groups()
                        exit_date = date(int(year), int(month), int(day))
                    except ValueError:
                        pass

            rows_data.append({
                'client': client_cache[client_name],
                'branch': branch_cache[cache_key],
                'product': product_cache[sku],
                'quantity': quantity,
                'exit_date': exit_date
            })

        if errors:
            return {
                'success': False,
                'message': 'El archivo tiene errores de validación',
                'errors': errors,
                'created_orders': []
            }

        if not rows_data:
            return {
                'success': False,
                'message': 'No hay datos para importar',
                'errors': ['El archivo no contiene datos válidos'],
                'created_orders': []
            }

    except Exception as e:
        return {
            'success': False,
            'message': 'Error al procesar el archivo',
            'errors': [str(e)],
            'created_orders': []
        }

    # Agrupar por cliente + sucursal
    orders_to_create = {}
    for row in rows_data:
        key = f"{row['client'].id}_{row['branch'].id if row['branch'] else 'no_branch'}"
        if key not in orders_to_create:
            orders_to_create[key] = {
                'client': row['client'],
                'branch': row['branch'],
                'exit_date': exit_date_override or row['exit_date'] or date.today(),
                'products': []
            }
        orders_to_create[key]['products'].append({
            'product': row['product'],
            'quantity': row['quantity']
        })

    created_orders = []
    errors = []

    try:
        for order_data in orders_to_create.values():
            # Crear la orden de salida
            exit_order = Exit.objects.create(
                client=order_data['client'],
                branch=order_data['branch'],
                exit_date=order_data['exit_date'],
                status='pendiente',
                created_by=user,
                updated_by=user
            )

            # Crear productos de la orden usando generate_exit_products
            for product_data in order_data['products']:
                try:
                    from .models import ExitProductGroup

                    # Crear el grupo de productos
                    exit_product_group = ExitProductGroup.objects.create(
                        exit=exit_order,
                        product=product_data['product'],
                        quantity=product_data['quantity'],
                        created_by=user,
                        updated_by=user
                    )

                    # Generar los ExitProducts (asignar bins)
                    exit_product_group.generate_exit_products(user=user)

                except Exception as e:
                    errors.append(
                        f"Error al crear producto {product_data['product'].name} en orden {exit_order.code}: {str(e)}")

            created_orders.append({
                'id': exit_order.id,
                'code': exit_order.code,
                'client': exit_order.client.name,
                'branch': exit_order.branch.name if exit_order.branch else 'Sin sucursal',
                'exit_date': exit_order.exit_date.strftime('%d/%m/%Y') if exit_order.exit_date else None,
                'products_count': len(order_data['products'])
            })

        return {
            'success': True,
            'message': f"Se crearon {len(created_orders)} órdenes de salida exitosamente",
            'errors': errors,
            'created_orders': created_orders
        }

    except Exception as e:
        raise TransactionError(f"Error al importar órdenes: {str(e)}")
