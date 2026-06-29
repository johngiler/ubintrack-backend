"""
Management command para pasar bins de EN_RECIBO a ALMACENADO de forma masiva.

Útil cuando hay muchos bins recién ingresados con posición asignada
que necesitan ser almacenados rápidamente.

Uso:
    python manage.py almacenar_bins --dry-run     # Ver qué se haría
    python manage.py almacenar_bins               # Ejecutar
    python manage.py almacenar_bins --client=5    # Solo un cliente
    python manage.py almacenar_bins --entry=123   # Solo una entrada específica
"""

from django.core.management.base import BaseCommand

from apps.inventory.models import Bin
from apps.core.choices import BinStatus


class Command(BaseCommand):
    help = 'Pasa bins de EN_RECIBO a ALMACENADO (requiere posición asignada)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo mostrar lo que se haría, sin hacer cambios',
        )
        parser.add_argument(
            '--client',
            type=int,
            help='ID de un cliente específico',
        )
        parser.add_argument(
            '--entry',
            type=int,
            help='ID de una orden de entrada específica',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Incluir bins SIN posición asignada (los almacena igual)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        client_id = options.get('client')
        entry_id = options.get('entry')
        include_all = options.get('all')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '=== MODO DRY-RUN: No se harán cambios ===\n'))

        # Buscar bins en EN_RECIBO
        bins_qs = Bin.objects.filter(
            status=BinStatus.EN_RECIBO,
            is_active=True
        ).select_related('client', 'position', 'entry')

        # Filtrar por posición si no se usa --all
        if not include_all:
            bins_qs = bins_qs.filter(position__isnull=False)

        if client_id:
            bins_qs = bins_qs.filter(client_id=client_id)

        if entry_id:
            bins_qs = bins_qs.filter(entry_id=entry_id)

        # Ordenar por cliente y código
        bins_qs = bins_qs.order_by('client__name', 'code')

        total_bins = bins_qs.count()

        if total_bins == 0:
            msg = 'No se encontraron bins en EN_RECIBO'
            if not include_all:
                msg += ' con posición asignada'
            self.stdout.write(self.style.SUCCESS(msg + '.'))
            return

        self.stdout.write(f'\nBins a almacenar: {total_bins}')
        self.stdout.write(f'{"="*70}\n')

        current_client = None
        updated_count = 0

        for bin in bins_qs:
            # Mostrar encabezado de cliente si cambia
            if current_client != bin.client_id:
                current_client = bin.client_id
                self.stdout.write(f'\n  [{bin.client.name}]')

            pos_code = bin.position.code if bin.position else 'SIN POSICIÓN'
            entry_code = bin.entry.code if bin.entry else 'N/A'

            self.stdout.write(
                f'    {bin.code} | Pos: {pos_code} | Entrada: {entry_code}'
            )

            if not dry_run:
                bin.status = BinStatus.ALMACENADO
                bin.save(update_fields=['status', 'updated_at'])
                updated_count += 1

        # Resumen final
        self.stdout.write(f'\n{"="*70}')
        self.stdout.write(self.style.SUCCESS('RESUMEN:'))
        self.stdout.write(f'  - Bins encontrados: {total_bins}')
        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(f'  - Bins almacenados: {updated_count}'))
        self.stdout.write(f'{"="*70}')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\n=== MODO DRY-RUN: No se hicieron cambios ==='))
            self.stdout.write(
                'Ejecuta sin --dry-run para aplicar los cambios.')
