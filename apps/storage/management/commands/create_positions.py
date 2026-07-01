import re
from django.core.management.base import BaseCommand
from apps.storage.models import Position, Cava
from apps.storage import services
from apps.core.choices import PositionStatus


class Command(BaseCommand):
    help = 'Crea posiciones a partir de una lista de códigos'

    def add_arguments(self, parser):
        parser.add_argument(
            '--positions',
            type=str,
            required=True,
            help='Lista de códigos de posiciones separados por comas'
        )

    def handle(self, *args, **options):
        position_codes = options['positions'].split(',')

        # Regex modificado para validar formatos como: C1-PA-R16-N1A
        pattern = r'^([A-Z0-9]{2})-P([A-Z])-R(\d+)-N(\d+)([A-Z])$'

        created_count = 0
        skipped_count = 0

        for code in position_codes:
            code = code.strip()

            # Verificar si la posición ya existe
            if Position.objects.filter(code=code).exists():
                self.stdout.write(self.style.WARNING(
                    f'La posición {code} ya existe. Omitiendo.'))
                skipped_count += 1
                continue

            # Validar formato del código
            match = re.match(pattern, code)
            if not match:
                self.stdout.write(self.style.ERROR(
                    f'Formato de código inválido: {code}'))
                skipped_count += 1
                continue

            # Extraer información del código
            cava_code, hall, rack, level, place = match.groups()

            # Buscar la cava correspondiente
            try:
                # Asumimos que las primeras dos letras son las iniciales del nombre de la cava
                cavas = Cava.objects.filter(is_active=True)
                cava = None

                for c in cavas:
                    if services.cava_code_prefix(c.name) == cava_code:
                        cava = c
                        break

                if not cava:
                    self.stdout.write(self.style.ERROR(
                        f'No se encontró cava para el código: {cava_code}'))
                    skipped_count += 1
                    continue

                # Crear la posición
                position = Position(
                    cava=cava,
                    hall=hall,
                    rack=int(rack),
                    level=int(level),
                    place=place,
                    status=PositionStatus.LIBRE,
                    code=code
                )
                position.save()

                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'Posición creada: {code}'))

            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f'Error al crear posición {code}: {str(e)}'))
                skipped_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'Proceso completado. Creadas: {created_count}, Omitidas: {skipped_count}'))
