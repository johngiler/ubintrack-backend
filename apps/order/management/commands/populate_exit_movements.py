"""
Management command para poblar el historial de movimientos de órdenes de salida.
Crea registros de ExitMovement basados en los datos existentes.

Uso:
    python manage.py populate_exit_movements
    python manage.py populate_exit_movements --dry-run
    python manage.py populate_exit_movements --clear  # Borra movimientos existentes primero
    python manage.py populate_exit_movements --exit-code SALIDA-00001  # Solo para una orden
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from apps.order.models import (
    Exit, ExitProductGroup, ExitProduct,
    ExitMovement, ExitMovementType, OrderStatus
)


class Command(BaseCommand):
    help = 'Poblar el historial de movimientos de órdenes de salida con datos existentes'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simular ejecución sin crear registros'
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Borrar movimientos existentes antes de poblar'
        )
        parser.add_argument(
            '--exit-code',
            type=str,
            help='Código de una orden específica a procesar'
        )
        parser.add_argument(
            '--only-missing',
            action='store_true',
            help='Solo crear movimientos para órdenes sin historial'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        clear = options['clear']
        exit_code = options['exit_code']
        only_missing = options['only_missing']

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '=== MODO DRY-RUN - No se crearán registros ==='))

        # Estadísticas
        stats = {
            'exits_processed': 0,
            'movements_created': 0,
            'created_movements': 0,
            'product_added_movements': 0,
            'bin_assigned_movements': 0,
            'bin_processed_movements': 0,
            'status_changed_movements': 0,
            'errors': 0
        }

        # Obtener órdenes de salida
        exits_qs = Exit.objects.all().select_related(
            'client', 'branch', 'created_by', 'updated_by'
        ).prefetch_related(
            'exit_product_groups__product',
            'exit_product_groups__exit_products__bin_product__bin',
            'exit_product_groups__exit_products__bin_product__bin__position',
            'exit_product_groups__exit_products__bin_product__entry_product__product',
            'exit_product_groups__exit_products__created_by',
            'exit_product_groups__exit_products__updated_by',
        )

        if exit_code:
            exits_qs = exits_qs.filter(code=exit_code)
            if not exits_qs.exists():
                self.stdout.write(self.style.ERROR(
                    f'No se encontró la orden {exit_code}'))
                return

        if only_missing:
            # Solo órdenes sin movimientos
            exits_with_movements = ExitMovement.objects.values_list(
                'exit_id', flat=True).distinct()
            exits_qs = exits_qs.exclude(id__in=exits_with_movements)

        total_exits = exits_qs.count()
        self.stdout.write(f'Procesando {total_exits} órdenes de salida...\n')

        # Borrar movimientos existentes si se solicita
        if clear and not dry_run:
            if exit_code:
                deleted, _ = ExitMovement.objects.filter(
                    exit__code=exit_code).delete()
            else:
                deleted, _ = ExitMovement.objects.all().delete()
            self.stdout.write(self.style.WARNING(
                f'Se eliminaron {deleted} movimientos existentes'))

        if dry_run:
            # En dry-run, usar una única transacción que se revierte al final
            try:
                with transaction.atomic():
                    for exit_obj in exits_qs:
                        try:
                            movements = self._create_movements_for_exit(
                                exit_obj, stats, dry_run)
                            stats['exits_processed'] += 1
                            stats['movements_created'] += len(movements)

                            if stats['exits_processed'] % 50 == 0:
                                self.stdout.write(
                                    f'  Procesadas {stats["exits_processed"]}/{total_exits} órdenes...')

                        except Exception as e:
                            stats['errors'] += 1
                            self.stdout.write(
                                self.style.ERROR(
                                    f'Error procesando {exit_obj.code}: {str(e)}')
                            )

                    # Revertir todo en dry-run
                    raise DryRunException('Dry run completado')
            except DryRunException:
                pass  # Esperado en dry-run
        else:
            # En modo real, cada orden en su propia transacción
            for exit_obj in exits_qs:
                try:
                    with transaction.atomic():
                        movements = self._create_movements_for_exit(
                            exit_obj, stats, dry_run=False)
                        ExitMovement.objects.bulk_create(movements)
                        stats['movements_created'] += len(movements)

                    stats['exits_processed'] += 1

                    if stats['exits_processed'] % 50 == 0:
                        self.stdout.write(
                            f'  Procesadas {stats["exits_processed"]}/{total_exits} órdenes...')

                except Exception as e:
                    stats['errors'] += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f'Error procesando {exit_obj.code}: {str(e)}')
                    )

        # Mostrar resumen
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS('RESUMEN DE EJECUCIÓN'))
        self.stdout.write('=' * 60)
        self.stdout.write(
            f'  Órdenes procesadas:         {stats["exits_processed"]}')
        self.stdout.write(
            f'  Movimientos creados:        {stats["movements_created"]}')
        self.stdout.write('')
        self.stdout.write('  Desglose por tipo:')
        self.stdout.write(
            f'    - Orden Creada:           {stats["created_movements"]}')
        self.stdout.write(
            f'    - Producto Agregado:      {stats["product_added_movements"]}')
        self.stdout.write(
            f'    - Bin Asignado:           {stats["bin_assigned_movements"]}')
        self.stdout.write(
            f'    - Bin Procesado:          {stats["bin_processed_movements"]}')
        self.stdout.write(
            f'    - Cambio de Estado:       {stats["status_changed_movements"]}')
        self.stdout.write('')
        if stats['errors'] > 0:
            self.stdout.write(self.style.ERROR(
                f'  Errores:                    {stats["errors"]}'))
        self.stdout.write('=' * 60)

    def _create_movements_for_exit(self, exit_obj, stats, dry_run):
        """Crear todos los movimientos para una orden de salida."""
        movements = []

        # 1. Movimiento de creación (CREATED)
        movement = ExitMovement(
            exit=exit_obj,
            movement_type=ExitMovementType.CREATED,
            new_status=OrderStatus.PENDIENTE,
            notes=f'Orden de salida {exit_obj.code} creada.',
            created_by=exit_obj.created_by,
            created_at=exit_obj.created_at,
            updated_at=exit_obj.created_at,
            metadata={
                'client': exit_obj.client.name if exit_obj.client else None,
                'branch': exit_obj.branch.name if exit_obj.branch else None,
                'exit_date': str(exit_obj.exit_date) if exit_obj.exit_date else None,
                'populated_by_script': True
            }
        )
        movements.append(movement)
        stats['created_movements'] += 1

        # 2. Movimientos de productos agregados (PRODUCT_ADDED)
        for group in exit_obj.exit_product_groups.all():
            product = group.product
            product_movements = ExitMovement(
                exit=exit_obj,
                movement_type=ExitMovementType.PRODUCT_ADDED,
                product=product,
                product_sku=product.client_sku if product else None,
                product_name=product.name if product else None,
                quantity=group.quantity,
                notes=f'Producto "{product.name if product else "N/A"}" agregado con cantidad {group.quantity}.',
                created_by=group.created_by if hasattr(
                    group, 'created_by') else exit_obj.created_by,
                created_at=group.created_at,
                updated_at=group.created_at,
                metadata={
                    'group_id': group.id,
                    'bin_or_batch': group.bin_or_batch,
                    'populated_by_script': True
                }
            )
            movements.append(product_movements)
            stats['product_added_movements'] += 1

            # 3. Movimientos de bins asignados (BIN_ASSIGNED)
            for exit_product in group.exit_products.all():
                bep = exit_product.bin_product
                bin_obj = bep.bin if bep else None
                position = bin_obj.position if bin_obj else None

                bin_movement = ExitMovement(
                    exit=exit_obj,
                    movement_type=ExitMovementType.BIN_ASSIGNED,
                    product=product,
                    product_sku=product.client_sku if product else None,
                    product_name=product.name if product else None,
                    bin=bin_obj,
                    bin_code=bin_obj.code if bin_obj else None,
                    quantity=exit_product.quantity,
                    is_picking=exit_product.is_picking,
                    notes=f'Bin {bin_obj.code if bin_obj else "N/A"} asignado con {exit_product.quantity} unidades{"(picking)" if exit_product.is_picking else ""}.',
                    created_by=exit_product.created_by if hasattr(
                        exit_product, 'created_by') else exit_obj.created_by,
                    created_at=exit_product.created_at,
                    updated_at=exit_product.created_at,
                    metadata={
                        'exit_product_id': exit_product.id,
                        'position': position.code if position else None,
                        'batch': bep.batch if bep else None,
                        'expiration_date': str(bep.expiration_date) if bep and bep.expiration_date else None,
                        'populated_by_script': True
                    }
                )
                movements.append(bin_movement)
                stats['bin_assigned_movements'] += 1

                # 4. Si el bin está procesado, crear movimiento de procesamiento (BIN_PROCESSED)
                if exit_product.is_processed:
                    processed_movement = ExitMovement(
                        exit=exit_obj,
                        movement_type=ExitMovementType.BIN_PROCESSED,
                        product=product,
                        product_sku=product.client_sku if product else None,
                        product_name=product.name if product else None,
                        bin=bin_obj,
                        bin_code=bin_obj.code if bin_obj else None,
                        quantity=exit_product.quantity,
                        is_picking=exit_product.is_picking,
                        notes=f'Bin {bin_obj.code if bin_obj else "N/A"} procesado con {exit_product.quantity} unidades.',
                        created_by=exit_product.updated_by if hasattr(
                            exit_product, 'updated_by') and exit_product.updated_by else exit_obj.created_by,
                        # El procesamiento ocurrió en algún momento después de la asignación
                        created_at=exit_product.updated_at if exit_product.updated_at else exit_product.created_at,
                        updated_at=exit_product.updated_at if exit_product.updated_at else exit_product.created_at,
                        metadata={
                            'exit_product_id': exit_product.id,
                            'was_qr_validated': exit_product.was_qr_validated,
                            'position': position.code if position else None,
                            'populated_by_script': True
                        }
                    )
                    movements.append(processed_movement)
                    stats['bin_processed_movements'] += 1

        # 5. Movimientos de cambio de estado (STATUS_CHANGED) - inferir estados
        self._add_status_movements(exit_obj, movements, stats)

        return movements

    def _add_status_movements(self, exit_obj, movements, stats):
        """
        Inferir y agregar movimientos de cambio de estado basados en el estado actual.
        """
        current_status = exit_obj.status

        # Si está en_progreso, hubo un cambio de pendiente a en_progreso
        if current_status in [OrderStatus.EN_PROGRESO, OrderStatus.COMPLETADA]:
            # Buscar el primer bin procesado para estimar cuándo cambió a en_progreso
            first_processed = None
            for group in exit_obj.exit_product_groups.all():
                for ep in group.exit_products.all():
                    if ep.is_processed:
                        if first_processed is None or ep.updated_at < first_processed:
                            first_processed = ep.updated_at

            status_change_time = first_processed or exit_obj.updated_at or exit_obj.created_at

            movement = ExitMovement(
                exit=exit_obj,
                movement_type=ExitMovementType.STATUS_CHANGED,
                old_status=OrderStatus.PENDIENTE,
                new_status=OrderStatus.EN_PROGRESO,
                notes=f'Estado cambiado de Pendiente a En Progreso.',
                created_by=exit_obj.updated_by if hasattr(
                    exit_obj, 'updated_by') and exit_obj.updated_by else exit_obj.created_by,
                created_at=status_change_time,
                updated_at=status_change_time,
                metadata={'populated_by_script': True, 'inferred': True}
            )
            movements.append(movement)
            stats['status_changed_movements'] += 1

        # Si está completada, hubo otro cambio a completada
        if current_status == OrderStatus.COMPLETADA:
            # Usar la fecha de actualización de la orden
            completed_time = exit_obj.updated_at or timezone.now()

            movement = ExitMovement(
                exit=exit_obj,
                movement_type=ExitMovementType.STATUS_CHANGED,
                old_status=OrderStatus.EN_PROGRESO,
                new_status=OrderStatus.COMPLETADA,
                notes=f'Estado cambiado de En Progreso a Completada.',
                created_by=exit_obj.updated_by if hasattr(
                    exit_obj, 'updated_by') and exit_obj.updated_by else exit_obj.created_by,
                created_at=completed_time,
                updated_at=completed_time,
                metadata={'populated_by_script': True, 'inferred': True}
            )
            movements.append(movement)
            stats['status_changed_movements'] += 1

        # Si está cancelada
        if current_status == OrderStatus.CANCELADA:
            cancelled_time = exit_obj.updated_at or timezone.now()

            # Determinar desde qué estado se canceló
            # Si tiene bins procesados, probablemente estaba en_progreso
            has_processed = any(
                ep.is_processed
                for group in exit_obj.exit_product_groups.all()
                for ep in group.exit_products.all()
            )
            old_status = OrderStatus.EN_PROGRESO if has_processed else OrderStatus.PENDIENTE

            movement = ExitMovement(
                exit=exit_obj,
                movement_type=ExitMovementType.STATUS_CHANGED,
                old_status=old_status,
                new_status=OrderStatus.CANCELADA,
                notes=f'Estado cambiado de {old_status} a Cancelada.',
                created_by=exit_obj.updated_by if hasattr(
                    exit_obj, 'updated_by') and exit_obj.updated_by else exit_obj.created_by,
                created_at=cancelled_time,
                updated_at=cancelled_time,
                metadata={'populated_by_script': True, 'inferred': True}
            )
            movements.append(movement)
            stats['status_changed_movements'] += 1


class DryRunException(Exception):
    """Excepción para abortar transacción en modo dry-run."""
    pass
