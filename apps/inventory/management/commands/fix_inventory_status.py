from django.core.management.base import BaseCommand
from django.db import transaction
from apps.order.models import EntryProduct


class Command(BaseCommand):
    help = 'Corrige el estado de inventario de todos los productos de entrada basándose en las cantidades actuales en bins'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Ejecuta sin guardar cambios (solo muestra lo que haría)',
        )
        parser.add_argument(
            '--entry-product-id',
            type=int,
            help='ID específico de un EntryProduct para corregir (opcional)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        entry_product_id = options.get('entry_product_id')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                'MODO DRY-RUN: No se guardarán cambios'))

        # Obtener productos a procesar
        if entry_product_id:
            entry_products = EntryProduct.objects.filter(id=entry_product_id)
            if not entry_products.exists():
                self.stdout.write(self.style.ERROR(
                    f'EntryProduct con ID {entry_product_id} no encontrado'))
                return
        else:
            entry_products = EntryProduct.objects.all()

        total_products = entry_products.count()
        self.stdout.write(
            f'Procesando {total_products} productos de entrada...\n')

        fixed_count = 0
        correct_count = 0
        inconsistencies = []
        excess_cases = []
        zero_quantity_bins = []

        with transaction.atomic():
            for entry_product in entry_products:
                # Calcular la cantidad total almacenada (bins actuales + despachado)
                from django.db.models import Sum
                from apps.order.models import ExitProduct

                total_in_bins = entry_product.bin_products.filter(
                    is_active=True
                ).aggregate(total=Sum('quantity'))['total'] or 0

                total_despachado = ExitProduct.objects.filter(
                    bin_product__entry_product=entry_product,
                    is_processed=True
                ).aggregate(total=Sum('quantity'))['total'] or 0

                # También considerar bins inactivos para el cálculo (pueden tener cantidad despachada)
                total_in_bins_inactive = entry_product.bin_products.filter(
                    is_active=False
                ).aggregate(total=Sum('quantity'))['total'] or 0

                # Calcular total almacenado: bins activos + bins inactivos + despachado
                # Los bins inactivos pueden tener cantidad si fueron desactivados después de despachar
                total_almacenado = total_in_bins + total_in_bins_inactive + total_despachado

                # Obtener el valor actual
                current_in_inventory = entry_product.in_inventory

                # Verificar si hay inconsistencia

                if current_in_inventory != total_almacenado:
                    inconsistencies.append({
                        'entry_product': entry_product,
                        'current': current_in_inventory,
                        'correct': total_almacenado,
                        'total_in_bins': total_in_bins,
                        'total_in_bins_inactive': total_in_bins_inactive,
                        'total_despachado': total_despachado,
                        'real_quantity': entry_product.real_quantity,
                        'is_completed': entry_product.is_completed,
                    })

                    if not dry_run:
                        # Actualizar el inventario
                        entry_product.in_inventory = total_almacenado

                        # Actualizar el estado de completado
                        if total_almacenado >= entry_product.real_quantity:
                            entry_product.is_completed = True
                        else:
                            entry_product.is_completed = False

                        entry_product.save(
                            update_fields=['in_inventory', 'is_completed'])

                    fixed_count += 1

                    # Verificar si hay exceso (in_inventory > real_quantity)
                    if total_almacenado > entry_product.real_quantity:
                        excess_cases.append({
                            'entry_product': entry_product,
                            'in_inventory': total_almacenado,
                            'real_quantity': entry_product.real_quantity,
                            'excess': total_almacenado - entry_product.real_quantity,
                            'total_in_bins': total_in_bins,
                            'total_despachado': total_despachado,
                        })
                    self.stdout.write(
                        self.style.WARNING(
                            f'✗ EntryProduct ID {entry_product.id} (Producto: {entry_product.product.name}): '
                            f'Inventario incorrecto: {current_in_inventory} → {total_almacenado} '
                            f'(En bins activos: {total_in_bins}, En bins inactivos: {total_in_bins_inactive}, Despachado: {total_despachado}, Real: {entry_product.real_quantity})'
                        )
                    )
                else:
                    correct_count += 1

                # Verificar bins con cantidad 0
                bins_with_zero = entry_product.bin_products.filter(
                    is_active=True,
                    quantity=0
                )
                if bins_with_zero.exists():
                    for bep in bins_with_zero:
                        zero_quantity_bins.append({
                            'entry_product': entry_product,
                            'bin': bep.bin,
                            'bin_product': bep,
                        })

        # Resumen
        self.stdout.write('\n' + '='*80)
        self.stdout.write(self.style.SUCCESS(f'\nResumen:'))
        self.stdout.write(f'  Total procesados: {total_products}')
        self.stdout.write(f'  Correctos: {correct_count}')
        self.stdout.write(f'  Corregidos: {fixed_count}')

        if inconsistencies:
            self.stdout.write(
                '\n' + self.style.WARNING('Inconsistencias encontradas:'))
            for inc in inconsistencies:
                ep = inc['entry_product']
                self.stdout.write(
                    f'  - EntryProduct ID {ep.id}: {ep.product.name} '
                    f'(Entrada: {ep.entry.code})'
                )
                self.stdout.write(
                    f'    Inventario: {inc["current"]} → {inc["correct"]} '
                    f'(Real: {inc["real_quantity"]})'
                )
                self.stdout.write(
                    f'    Desglose: En bins activos: {inc["total_in_bins"]}, En bins inactivos: {inc.get("total_in_bins_inactive", 0)}, Despachado: {inc["total_despachado"]}'
                )

                # Mostrar información de ExitProducts relacionados
                from apps.order.models import ExitProduct
                exit_products = ExitProduct.objects.filter(
                    bin_product__entry_product=ep,
                    is_processed=True
                ).select_related('bin_product', 'exit_product_group__exit')
                if exit_products.exists():
                    self.stdout.write(
                        f'    ExitProducts procesados encontrados: {exit_products.count()}')
                    for exp in exit_products:
                        self.stdout.write(
                            f'      - Exit: {exp.exit_product_group.exit.code}, '
                            f'Cantidad: {exp.quantity}, '
                            f'Bin: {exp.bin_product.bin.code}'
                        )
                else:
                    self.stdout.write(
                        '    No se encontraron ExitProducts procesados')

                # Mostrar información de bins
                bins_info = ep.bin_products.filter(is_active=True)
                self.stdout.write(f'    Bins activos: {bins_info.count()}')
                for bep in bins_info:
                    self.stdout.write(
                        f'      - {bep.bin.code}: {bep.quantity} {ep.product.uom}'
                    )
                self.stdout.write('')

        if excess_cases:
            self.stdout.write(
                '\n' + self.style.ERROR('⚠️  CASOS CON EXCESO (in_inventory > real_quantity):'))
            self.stdout.write(
                '   Estos casos requieren atención manual para corregir las cantidades en bins.\n')
            for exc in excess_cases:
                ep = exc['entry_product']
                self.stdout.write(
                    f'  - EntryProduct ID {ep.id}: {ep.product.name} '
                    f'(Entrada: {ep.entry.code})'
                )
                self.stdout.write(
                    f'    Inventario: {exc["in_inventory"]} > Real: {exc["real_quantity"]} '
                    f'(Exceso: {exc["excess"]})'
                )
                self.stdout.write(
                    f'    Desglose: En bins: {exc["total_in_bins"]}, Despachado: {exc["total_despachado"]}'
                )

                # Mostrar información de bins
                bins_info = ep.bin_products.filter(is_active=True)
                self.stdout.write(f'    Bins activos: {bins_info.count()}')
                for bep in bins_info:
                    self.stdout.write(
                        f'      - {bep.bin.code}: {bep.quantity} {ep.product.uom}'
                    )
                self.stdout.write('')

        if zero_quantity_bins:
            self.stdout.write(
                '\n' + self.style.WARNING('⚠️  BINS CON CANTIDAD 0:'))
            self.stdout.write(
                '   Estos bins tienen cantidad 0 y deberían ser revisados o eliminados.\n')
            for zqb in zero_quantity_bins:
                ep = zqb['entry_product']
                bin = zqb['bin']
                self.stdout.write(
                    f'  - EntryProduct ID {ep.id}: {ep.product.name} '
                    f'(Entrada: {ep.entry.code})'
                )
                self.stdout.write(
                    f'    Bin: {bin.code} (ID: {bin.id}) - Cantidad: 0 {ep.product.uom}'
                )
                self.stdout.write('')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\nMODO DRY-RUN: No se guardaron cambios. Ejecuta sin --dry-run para aplicar correcciones.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                '\n✓ Correcciones aplicadas exitosamente.'))
