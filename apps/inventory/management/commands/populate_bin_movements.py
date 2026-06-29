from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from apps.inventory.models import Bin, BinMovement, BinMovementType
from apps.order.models import ExitProduct
from apps.core import choices


class Command(BaseCommand):
    help = 'Pobla movimientos históricos de bins basándose en datos existentes'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Ejecuta sin guardar cambios (solo muestra lo que haría)',
        )
        parser.add_argument(
            '--bin-id',
            type=int,
            help='ID específico de un bin para poblar (opcional)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        bin_id = options.get('bin_id')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                'MODO DRY-RUN: No se guardarán cambios'))

        # Obtener bins a procesar
        if bin_id:
            bins = Bin.objects.filter(id=bin_id)
            if not bins.exists():
                self.stdout.write(self.style.ERROR(
                    f'Bin con ID {bin_id} no encontrado'))
                return
        else:
            bins = Bin.objects.all()

        total_bins = bins.count()
        self.stdout.write(f'Procesando {total_bins} bins...')

        created_count = 0
        status_changed_count = 0
        exit_processed_count = 0
        skipped_count = 0

        with transaction.atomic():
            for bin in bins:
                # Verificar si ya tiene movimientos
                existing_movements = bin.movements.count()
                if existing_movements > 0:
                    self.stdout.write(
                        self.style.WARNING(
                            f'Bin {bin.code} ya tiene {existing_movements} movimientos. Saltando...'
                        )
                    )
                    skipped_count += 1
                    continue

                # 1. Crear movimiento de creación
                if not dry_run:
                    BinMovement.objects.create(
                        bin=bin,
                        movement_type=BinMovementType.CREATED,
                        new_status=bin.status,
                        notes=f"Bin creado para la orden de entrada {bin.entry.code}",
                        created_at=bin.created_at,
                        created_by=bin.created_by,
                    )
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'✓ Bin {bin.code}: Movimiento de creación creado')
                )

                # 2. Si el estado actual no es el inicial (en_recibo), crear movimiento de cambio de estado
                if bin.status != choices.BinStatus.EN_RECIBO:
                    # Usar updated_at como fecha aproximada del cambio de estado
                    # o dispatched_date si está despachado
                    change_date = bin.dispatched_date if bin.status == choices.BinStatus.DESPACHADO else bin.updated_at

                    if not dry_run:
                        BinMovement.objects.create(
                            bin=bin,
                            movement_type=BinMovementType.STATUS_CHANGED,
                            old_status=choices.BinStatus.EN_RECIBO,
                            new_status=bin.status,
                            notes=f"Estado cambiado de {choices.BinStatus.EN_RECIBO} a {bin.status}",
                            created_at=change_date or bin.updated_at,
                            created_by=bin.updated_by or bin.created_by,
                        )
                    status_changed_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'  → Cambio de estado a {bin.status} registrado'
                        )
                    )

                # 3. Buscar salidas procesadas relacionadas con este bin
                # Obtener todos los ExitProducts procesados que usan productos de este bin
                bin_products = bin.bin_products.all()

                for bin_product in bin_products:
                    exit_products = ExitProduct.objects.filter(
                        bin_product=bin_product,
                        is_processed=True
                    ).select_related('exit_product_group__exit').order_by('updated_at')

                    for exit_product in exit_products:
                        exit_obj = exit_product.exit_product_group.exit

                        # Verificar si ya existe un movimiento para esta salida
                        if not dry_run:
                            existing_exit_movement = BinMovement.objects.filter(
                                bin=bin,
                                movement_type=BinMovementType.EXIT_PROCESSED,
                                exit=exit_obj,
                                exit_code=exit_obj.code
                            ).exists()

                            if existing_exit_movement:
                                continue

                        # Calcular cantidad restante (aproximada)
                        # La cantidad restante sería la cantidad actual del bin_product
                        quantity_remaining = bin_product.quantity
                        quantity_subtracted = exit_product.quantity

                        if not dry_run:
                            BinMovement.objects.create(
                                bin=bin,
                                movement_type=BinMovementType.EXIT_PROCESSED,
                                exit=exit_obj,
                                exit_code=exit_obj.code,
                                is_picking=exit_product.is_picking,
                                quantity_subtracted=quantity_subtracted,
                                quantity_remaining=quantity_remaining,
                                notes=f"Salida procesada: {exit_obj.code} - {'Picking' if exit_product.is_picking else 'Completo'}",
                                created_at=exit_product.updated_at or exit_product.created_at,
                                created_by=exit_product.updated_by or exit_product.created_by,
                            )
                        exit_processed_count += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'  → Salida {exit_obj.code} procesada registrada'
                            )
                        )

        # Resumen
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS('RESUMEN:'))
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(f'Bins procesados: {total_bins}')
        self.stdout.write(f'Movimientos de creación: {created_count}')
        self.stdout.write(
            f'Movimientos de cambio de estado: {status_changed_count}')
        self.stdout.write(
            f'Movimientos de salidas procesadas: {exit_processed_count}')
        self.stdout.write(
            f'Bins saltados (ya tenían movimientos): {skipped_count}')

        if dry_run:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'MODO DRY-RUN: No se guardaron cambios'))
        else:
            total_movements = created_count + status_changed_count + exit_processed_count
            self.stdout.write('')
            self.stdout.write(
                self.style.SUCCESS(
                    f'✓ Total de movimientos creados: {total_movements}'
                )
            )
