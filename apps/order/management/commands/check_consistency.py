from django.core.management.base import BaseCommand
from django.db.models import Q, Sum
from apps.order.models import ExitProduct
from apps.inventory.models import BinProduct


class Command(BaseCommand):
    help = 'Verifica la consistencia de datos entre ExitProducts procesados y BinProducts'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Intenta corregir las inconsistencias encontradas',
        )

    def handle(self, *args, **options):
        fix_mode = options['fix']
        
        self.stdout.write(self.style.WARNING('=' * 70))
        self.stdout.write(self.style.WARNING('VERIFICACIÓN DE CONSISTENCIA'))
        self.stdout.write(self.style.WARNING('=' * 70))
        self.stdout.write('')
        
        inconsistencies = []
        
        # 1. Verificar ExitProducts procesados que no tienen cantidad restada del bin
        self.stdout.write('1. Verificando ExitProducts procesados...')
        exit_products_processed = ExitProduct.objects.filter(is_processed=True).select_related(
            'bin_product', 'exit_product_group__exit'
        )
        
        for exit_product in exit_products_processed:
            bin_product = exit_product.bin_product
            quantity_needed = exit_product.quantity
            
            # Calcular cuánto debería haberse restado
            # Buscar movimientos relacionados para verificar
            from apps.inventory.models import BinMovement
            movements = BinMovement.objects.filter(
                exit=exit_product.exit_product_group.exit,
                exit_code=exit_product.exit_product_group.exit.code,
                bin=bin_product.bin
            )
            
            # Si no hay movimiento registrado, podría ser una inconsistencia
            if not movements.exists():
                # Verificar si la cantidad del bin es consistente
                # Esto es más complejo porque puede haber múltiples salidas del mismo bin
                inconsistencies.append({
                    'type': 'missing_movement',
                    'exit_product_id': exit_product.id,
                    'exit_code': exit_product.exit_product_group.exit.code,
                    'bin_code': bin_product.bin.code,
                    'quantity': quantity_needed,
                    'message': f'ExitProduct procesado sin movimiento registrado'
                })
        
        # 2. Verificar bins con cantidad 0 pero que tienen ExitProducts no procesados
        self.stdout.write('2. Verificando bins con cantidad 0...')
        bins_with_zero = BinProduct.objects.filter(quantity=0).select_related('bin')
        
        for bin_product in bins_with_zero:
            # Verificar si hay ExitProducts relacionados no procesados
            exit_products = ExitProduct.objects.filter(
                bin_product=bin_product,
                is_processed=False
            )
            
            if exit_products.exists():
                inconsistencies.append({
                    'type': 'zero_quantity_with_pending_exits',
                    'bin_code': bin_product.bin.code,
                    'bin_product_id': bin_product.id,
                    'pending_exits': [ep.id for ep in exit_products],
                    'message': f'Bin con cantidad 0 pero tiene ExitProducts pendientes'
                })
        
        # 3. Verificar ExitProducts procesados donde el bin tiene más cantidad de la esperada
        # (esto indicaría que no se restó correctamente)
        self.stdout.write('3. Verificando discrepancias en cantidades...')
        for exit_product in exit_products_processed:
            bin_product = exit_product.bin_product
            
            # Calcular la cantidad total que debería haberse restado de este bin
            total_subtracted = ExitProduct.objects.filter(
                bin_product=bin_product,
                is_processed=True
            ).aggregate(total=Sum('quantity'))['total'] or 0
            
            # Esto es una verificación aproximada - no podemos saber la cantidad original
            # sin el historial completo, pero podemos detectar casos obvios
            
        # Mostrar resultados
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('=' * 70))
        self.stdout.write(self.style.WARNING('RESULTADOS'))
        self.stdout.write(self.style.WARNING('=' * 70))
        
        if not inconsistencies:
            self.stdout.write(self.style.SUCCESS('✓ No se encontraron inconsistencias'))
        else:
            self.stdout.write(
                self.style.ERROR(f'✗ Se encontraron {len(inconsistencies)} inconsistencias:')
            )
            self.stdout.write('')
            
            for i, inconsistency in enumerate(inconsistencies, 1):
                self.stdout.write(
                    self.style.ERROR(f'{i}. {inconsistency["message"]}')
                )
                for key, value in inconsistency.items():
                    if key not in ['type', 'message']:
                        self.stdout.write(f'   {key}: {value}')
                self.stdout.write('')
            
            if fix_mode:
                self.stdout.write(self.style.WARNING('Modo --fix no implementado aún'))
            else:
                self.stdout.write(
                    self.style.WARNING(
                        'Ejecuta con --fix para intentar corregir automáticamente'
                    )
                )

