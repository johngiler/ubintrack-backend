from django.core.management.base import BaseCommand
from django.db.models import Sum
from apps.inventory.models import BinMovement, Bin


class Command(BaseCommand):
    help = 'Poblar el campo initial_quantity para movimientos históricos de almacenamiento'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo mostrar qué se haría sin hacer cambios',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # Buscar movimientos de almacenado sin initial_quantity
        movements = BinMovement.objects.filter(
            new_status='almacenado',
            initial_quantity__isnull=True
        ).select_related('bin')

        total = movements.count()
        updated = 0

        self.stdout.write(
            f"Encontrados {total} movimientos de almacenado sin initial_quantity")

        for movement in movements:
            bin = movement.bin

            # Buscar movimientos de salida procesada para este bin
            exit_movements = BinMovement.objects.filter(
                bin=bin,
                movement_type='exit_processed'
            ).order_by('-created_at')

            if exit_movements.exists():
                # Calcular: suma de quantity_subtracted + quantity_remaining del último
                total_subtracted = exit_movements.aggregate(
                    total=Sum('quantity_subtracted')
                )['total'] or 0

                last_exit = exit_movements.first()
                last_remaining = last_exit.quantity_remaining or 0

                initial_quantity = total_subtracted + last_remaining
            else:
                # Sin salidas: usar la cantidad actual del bin
                initial_quantity = bin.bin_products.aggregate(
                    total=Sum('quantity')
                )['total'] or 0

            if dry_run:
                self.stdout.write(
                    f"  [DRY-RUN] {bin.code}: initial_quantity = {initial_quantity}"
                )
            else:
                movement.initial_quantity = initial_quantity
                movement.save(update_fields=['initial_quantity'])
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ {bin.code}: initial_quantity = {initial_quantity}")
                )

            updated += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"\n[DRY-RUN] Se habrían actualizado {updated} movimientos"))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\n✓ Actualizados {updated} movimientos"))
