"""
Management command para corregir conflictos de stock en órdenes de salida.

Uso:
    python manage.py fix_exit_conflicts --dry-run    # Solo mostrar, no hacer cambios
    python manage.py fix_exit_conflicts              # Ejecutar correcciones
    python manage.py fix_exit_conflicts --exit=123   # Solo para una orden específica
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from apps.order.models import Exit, ExitProduct, ExitProductGroup
from apps.inventory.models import BinProduct
from apps.core.choices import BinStatus, OrderStatus


class Command(BaseCommand):
    help = 'Corrige conflictos de stock en órdenes de salida y elimina bins vacíos'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo mostrar lo que se haría, sin hacer cambios',
        )
        parser.add_argument(
            '--exit',
            type=int,
            help='ID de una orden de salida específica',
        )
        parser.add_argument(
            '--client',
            type=int,
            help='ID de un cliente específico',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        exit_id = options.get('exit')
        client_id = options.get('client')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '=== MODO DRY-RUN: No se harán cambios ===\n'))

        # Filtrar órdenes de salida pendientes o en progreso
        exits_qs = Exit.objects.filter(
            status__in=[OrderStatus.PENDIENTE, OrderStatus.EN_PROGRESO]
        )

        if exit_id:
            exits_qs = exits_qs.filter(id=exit_id)
        if client_id:
            exits_qs = exits_qs.filter(client_id=client_id)

        exits_qs = exits_qs.select_related('client').prefetch_related(
            'exit_product_groups__exit_products__bin_product__bin',
            'exit_product_groups__exit_products__bin_product__entry_product__product'
        )

        total_conflicts_fixed = 0
        total_empty_deleted = 0
        total_reassigned = 0
        total_empty_groups = 0

        for exit_order in exits_qs:
            self.stdout.write(f'\n{"="*60}')
            self.stdout.write(
                f'Orden: {exit_order.code} - Cliente: {exit_order.client.name}')
            self.stdout.write(f'Estado: {exit_order.get_status_display()}')
            self.stdout.write(f'{"="*60}')

            # 1. Eliminar ExitProducts vacíos o con cantidad 0
            empty_deleted = self._delete_empty_exit_products(
                exit_order, dry_run)
            total_empty_deleted += empty_deleted

            # 2. Buscar y corregir conflictos de stock
            conflicts_fixed, reassigned = self._fix_stock_conflicts(
                exit_order, dry_run)
            total_conflicts_fixed += conflicts_fixed
            total_reassigned += reassigned

            # 3. Eliminar ExitProductGroups vacíos (sin ExitProducts)
            empty_groups_deleted = self._delete_empty_groups(
                exit_order, dry_run)
            total_empty_groups += empty_groups_deleted

        # 4. Limpiar grupos vacíos en órdenes COMPLETADAS también
        self.stdout.write(f'\n{"="*60}')
        self.stdout.write(self.style.WARNING(
            'LIMPIEZA DE ÓRDENES COMPLETADAS:'))
        self.stdout.write(f'{"="*60}')

        completed_exits_qs = Exit.objects.filter(
            status=OrderStatus.COMPLETADA
        )
        if exit_id:
            completed_exits_qs = completed_exits_qs.filter(id=exit_id)
        if client_id:
            completed_exits_qs = completed_exits_qs.filter(client_id=client_id)

        for exit_order in completed_exits_qs:
            empty_groups_deleted = self._delete_empty_groups(
                exit_order, dry_run)
            if empty_groups_deleted > 0:
                self.stdout.write(
                    f'  {exit_order.code}: {empty_groups_deleted} grupos vacíos eliminados')
                total_empty_groups += empty_groups_deleted

        # Resumen final
        self.stdout.write(f'\n{"="*60}')
        self.stdout.write(self.style.SUCCESS('RESUMEN:'))
        self.stdout.write(
            f'  - ExitProducts vacíos eliminados: {total_empty_deleted}')
        self.stdout.write(
            f'  - Conflictos detectados: {total_conflicts_fixed}')
        self.stdout.write(f'  - Bins reasignados: {total_reassigned}')
        self.stdout.write(
            f'  - Grupos vacíos eliminados: {total_empty_groups}')
        self.stdout.write(f'{"="*60}')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\n=== MODO DRY-RUN: No se hicieron cambios ==='))

    def _delete_empty_exit_products(self, exit_order, dry_run):
        """Elimina ExitProducts con cantidad 0 o sin bin asignado."""
        empty_products = ExitProduct.objects.filter(
            exit_product_group__exit=exit_order,
            is_processed=False
        ).filter(
            # Cantidad 0 o sin bin
            quantity__lte=0
        ) | ExitProduct.objects.filter(
            exit_product_group__exit=exit_order,
            is_processed=False,
            bin_product__isnull=True
        )

        count = empty_products.count()
        if count > 0:
            self.stdout.write(
                f'\n  [VACÍOS] Encontrados {count} ExitProducts vacíos')
            for ep in empty_products:
                self.stdout.write(
                    f'    - ID: {ep.id}, Cantidad: {ep.quantity}, Bin: {ep.bin_product}')

            if not dry_run:
                empty_products.delete()
                self.stdout.write(self.style.SUCCESS(
                    f'    ✓ Eliminados {count} ExitProducts vacíos'))

        return count

    def _fix_stock_conflicts(self, exit_order, dry_run):
        """Busca y corrige conflictos de stock."""
        conflicts_found = 0
        reassigned = 0

        # Obtener todos los ExitProducts no procesados de esta orden
        exit_products = ExitProduct.objects.filter(
            exit_product_group__exit=exit_order,
            is_processed=False
        ).select_related(
            'bin_product__bin',
            'bin_product__entry_product__product',
            'exit_product_group'
        )

        for ep in exit_products:
            if not ep.bin_product:
                continue

            bep = ep.bin_product
            product = bep.entry_product.product
            client = exit_order.client

            # Calcular reservas totales para este BinProduct
            total_reserved = ExitProduct.objects.filter(
                bin_product=bep,
                is_processed=False,
                exit_product_group__exit__status__in=[
                    OrderStatus.PENDIENTE, OrderStatus.EN_PROGRESO]
            ).aggregate(total=Sum('quantity'))['total'] or 0

            # Disponible sin contar este EP
            available = bep.quantity - (total_reserved - ep.quantity)

            # Verificar si hay conflicto
            if ep.quantity > available:
                conflicts_found += 1
                self.stdout.write(f'\n  [CONFLICTO] {bep.bin.code}')
                self.stdout.write(
                    f'    Producto: {product.name} ({product.client_sku})')
                self.stdout.write(
                    f'    Necesita: {ep.quantity}, Disponible: {available} (Total bin: {bep.quantity})')

                # Intentar encontrar un bin alternativo
                alternative = self._find_alternative_bin(
                    ep, product, client, exit_order)

                if alternative:
                    self.stdout.write(self.style.SUCCESS(
                        f'    → Alternativa: {alternative.bin.code} '
                        f'(Disponible: {alternative.quantity}, Pos: {alternative.bin.position.code if alternative.bin.position else "N/A"})'
                    ))

                    if not dry_run:
                        # Reasignar
                        ep.bin_product = alternative
                        ep.is_picking = alternative.quantity > ep.quantity
                        ep.save(update_fields=[
                                'bin_product', 'is_picking'])
                        reassigned += 1
                        self.stdout.write(self.style.SUCCESS(
                            f'    ✓ Reasignado a {alternative.bin.code}'))
                else:
                    self.stdout.write(self.style.ERROR(
                        '    ✗ No se encontró alternativa disponible'))

        return conflicts_found, reassigned

    def _find_alternative_bin(self, exit_product, product, client, exit_order):
        """Busca un bin alternativo con suficiente stock."""
        quantity_needed = exit_product.quantity
        original_batch = exit_product.bin_product.entry_product.batch if exit_product.bin_product else None

        # Bins ya usados como completos en otras salidas
        bins_completos = ExitProduct.objects.filter(
            exit_product_group__exit__client=client,
            exit_product_group__exit__status__in=[
                OrderStatus.PENDIENTE, OrderStatus.EN_PROGRESO],
            bin_product__entry_product__product=product,
            is_picking=False,
            is_processed=False
        ).values_list('bin_product__bin_id', flat=True)

        # Query base
        base_query = BinProduct.objects.filter(
            entry_product__product=product,
            entry_product__entry__client=client,
            bin__status=BinStatus.ALMACENADO,
            bin__is_active=True,
            quantity__gt=0
        ).exclude(
            bin_id__in=bins_completos
        ).exclude(
            id=exit_product.bin_product_id
        ).select_related('bin', 'bin__position', 'entry_product')

        # Calcular disponibilidad real para cada bin
        candidates = []
        for bep in base_query:
            reserved = ExitProduct.objects.filter(
                bin_product=bep,
                is_processed=False,
                exit_product_group__exit__status__in=[
                    OrderStatus.PENDIENTE, OrderStatus.EN_PROGRESO]
            ).aggregate(total=Sum('quantity'))['total'] or 0

            available = bep.quantity - reserved
            if available >= quantity_needed:
                same_batch = bep.entry_product.batch == original_batch if original_batch else False
                candidates.append({
                    'bep': bep,
                    'available': available,
                    'same_batch': same_batch,
                    'expiration': bep.entry_product.expiration_date
                })

        if not candidates:
            return None

        # Ordenar: primero mismo lote, luego por fecha de vencimiento
        from datetime import date
        max_date = date(9999, 12, 31)
        candidates.sort(key=lambda x: (
            not x['same_batch'], x['expiration'] or max_date))

        return candidates[0]['bep']

    def _delete_empty_groups(self, exit_order, dry_run):
        """Elimina ExitProductGroups sin ExitProducts."""
        empty_groups = ExitProductGroup.objects.filter(
            exit=exit_order
        ).annotate(
            ep_count=Sum('exit_products__quantity')
        ).filter(ep_count__isnull=True) | ExitProductGroup.objects.filter(
            exit=exit_order,
            exit_products__isnull=True
        )

        count = empty_groups.count()
        if count > 0:
            self.stdout.write(
                f'\n  [GRUPOS VACÍOS] Encontrados {count} ExitProductGroups vacíos')

            if not dry_run:
                empty_groups.delete()
                self.stdout.write(self.style.SUCCESS(
                    f'    ✓ Eliminados {count} grupos vacíos'))

        return count
