import os
from django.core.management.base import BaseCommand
from apps.storage.models import Position
from apps.common.utils.zebra import print_to_zebra


class Command(BaseCommand):
    help = 'Imprime etiquetas de posiciones en masa, 3 posiciones por etiqueta'

    def add_arguments(self, parser):
        parser.add_argument('--cava', type=str, help='Filtrar por cava (ej: "Cava 1")')
        parser.add_argument('--pasillo', type=str,
                            help='Filtrar por pasillo (ej: "A" para Pasillo A)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Solo mostrar ZPL sin imprimir')
        parser.add_argument('--output', type=str,
                            help='Ruta para guardar el archivo ZPL generado')
        parser.add_argument('--positions', type=str,
                            help='Lista de códigos de posición separados por comas (ej: "C1-PB-R1-N1A,C1-PB-R1-N1B")')

    def handle(self, *args, **options):
        # Obtener posiciones filtrando por cava y/o pasillo
        positions = Position.objects.all().order_by('id')

        # Aplicar filtro por códigos específicos si se especifica
        if options['positions']:
            position_codes = [code.strip() for code in options['positions'].split(',')]
            positions = positions.filter(code__in=position_codes)
            self.stdout.write(
                f"Filtrando por códigos de posición específicos: {len(position_codes)} códigos")

        # Aplicar filtro por cava si se especifica
        if options['cava']:
            positions = positions.filter(cava__name=options['cava'])
            self.stdout.write(f"Filtrando por cava: {options['cava']}")

        # Aplicar filtro por pasillo si se especifica
        if options['pasillo']:
            # El modelo almacena el pasillo como una sola letra (A, B, C, D)
            pasillo_letra = options['pasillo'].upper().strip()[0]
            positions = positions.filter(hall=pasillo_letra)
            self.stdout.write(
                f"Filtrando por pasillo: {pasillo_letra} (Pasillo {pasillo_letra})")

        total_positions = positions.count()
        self.stdout.write(self.style.SUCCESS(
            f'Encontradas {total_positions} posiciones'))

        # Procesar las posiciones en grupos de 3
        position_groups = []
        current_group = []

        for position in positions:
            current_group.append(position)

            if len(current_group) == 3:
                position_groups.append(current_group)
                current_group = []

        # Añadir el último grupo si tiene menos de 3 posiciones
        if current_group:
            position_groups.append(current_group)

        self.stdout.write(self.style.SUCCESS(
            f'Preparando {len(position_groups)} etiquetas'))

        for i, group in enumerate(position_groups):
            # Generar ZPL combinado para el grupo
            combined_zpl = self.generate_combined_zpl(group)

            if options['dry_run']:
                self.stdout.write(f"Etiqueta {i+1}:\n{combined_zpl}")
            elif options['output']:
                # Guardar a archivo si se especifica una ruta
                output_path = options['output']
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, 'a') as f:
                    f.write(combined_zpl + "\n")
                self.stdout.write(self.style.SUCCESS(f'ZPL guardado en {output_path}'))
            else:
                # Imprimir directamente
                success = print_to_zebra(combined_zpl)
                status = 'éxito' if success else 'error'
                self.stdout.write(
                    f"Imprimiendo etiqueta {i+1} de {len(position_groups)}: {status}")

        self.stdout.write(self.style.SUCCESS('Proceso completado'))

    def generate_combined_zpl(self, positions):
        """
        Genera un ZPL combinado para un grupo de hasta 3 posiciones,
        ajustando las coordenadas verticales para que no se superpongan.
        """
        if not positions:
            return ""

        # Dimensiones totales de la etiqueta
        total_width = 812   # 4 pulgadas a 203 DPI
        total_height = 1218  # 6 pulgadas a 203 DPI

        # Centro horizontal de la etiqueta
        center_x = total_width / 2

        # Altura disponible para cada posición
        block_height = total_height / 3

        # Iniciar el ZPL
        zpl = "^XA\n"
        zpl += f"^PW{total_width}    ; Ancho de la etiqueta (4 pulgadas)\n"
        zpl += f"^LL{total_height}   ; Largo de la etiqueta (6 pulgadas)\n"
        zpl += "^CF0,45   ; Fuente por defecto y tamaño\n\n"

        # Añadir cada posición con coordenadas ajustadas
        for i, position in enumerate(positions):
            # Obtener información de la posición
            code = position.code
            level = position.level

            # Calcular el inicio de este bloque
            block_start = i * block_height

            # Definir márgenes y espaciados dentro del bloque
            top_margin = 40
            title_y = block_start + top_margin
            qr_y = title_y + 60
            code_y = qr_y + 240

            # Tamaños de elementos - igual que en services.py
            nivel_width = 92   # Ancho aproximado del texto "NIVEL X"
            code_width = 190    # Ancho aproximado del código
            qr_width = 88     # Ancho aproximado del QR

            # Calcular posiciones horizontales para centrado perfecto - igual que en services.py
            title_x = center_x - nivel_width
            qr_x = center_x - qr_width
            code_x = center_x - code_width

            # Título: NIVEL
            zpl += f"; Título: NIVEL para posición {i+1}\n"
            zpl += f"^FO{title_x},{title_y}^A0N,55,55^FDNIVEL {level}^FS\n\n"

            # QR Code
            zpl += f"; QR Code para posición {i+1}\n"
            zpl += f"^FO{qr_x},{qr_y}^BQN,1,8^FDMA,{code}^FS\n\n"

            # Código de posición
            zpl += f"; Código para posición {i+1}\n"
            zpl += f"^FO{code_x},{code_y}^A0N,55,55^FD{code}^FS\n\n"

        # Cerrar el ZPL
        zpl += "^XZ"

        return zpl
