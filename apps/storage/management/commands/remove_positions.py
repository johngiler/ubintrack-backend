from django.core.management.base import BaseCommand
from apps.storage.models import Position


class Command(BaseCommand):
    help = 'Elimina posiciones a partir de una lista de códigos'

    def add_arguments(self, parser):
        parser.add_argument(
            '--positions',
            type=str,
            required=True,
            help='Lista de códigos de posiciones separados por comas'
        )

    def handle(self, *args, **options):
        position_codes = options['positions'].split(',')

        deleted_count = 0
        not_found_count = 0

        for code in position_codes:
            code = code.strip()

            try:
                position = Position.objects.get(code=code)
                position.delete()
                deleted_count += 1
                self.stdout.write(self.style.SUCCESS(f'Posición eliminada: {code}'))
            except Position.DoesNotExist:
                self.stdout.write(self.style.WARNING(f'Posición no encontrada: {code}'))
                not_found_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'Proceso completado. Eliminadas: {deleted_count}, No encontradas: {not_found_count}'))
