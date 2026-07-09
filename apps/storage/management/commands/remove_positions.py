from django.core.management.base import BaseCommand, CommandError

from apps.storage.models import Position
from apps.workspaces.models import Workspace


class Command(BaseCommand):
    help = 'Elimina posiciones a partir de una lista de códigos'

    def add_arguments(self, parser):
        parser.add_argument(
            '--positions',
            type=str,
            required=True,
            help='Lista de códigos de posiciones separados por comas'
        )
        parser.add_argument(
            '--workspace-slug',
            type=str,
            default=None,
            help='Slug del workspace (recomendado en entornos multi-tenant)',
        )

    def handle(self, *args, **options):
        position_codes = options['positions'].split(',')
        workspace_slug = options.get('workspace_slug')

        qs = Position.objects.select_related('cava__storage__workspace')
        if workspace_slug:
            ws = Workspace.objects.filter(slug=workspace_slug.strip().lower()).first()
            if not ws:
                raise CommandError(f'No existe workspace con slug "{workspace_slug}"')
            qs = qs.filter(cava__storage__workspace=ws)

        deleted_count = 0
        not_found_count = 0

        for code in position_codes:
            code = code.strip()

            matches = qs.filter(code=code)
            count = matches.count()

            if count == 0:
                self.stdout.write(self.style.WARNING(f'Posición no encontrada: {code}'))
                not_found_count += 1
                continue

            if count > 1:
                self.stdout.write(self.style.ERROR(
                    f'Posición ambigua ({count} coincidencias): {code}. '
                    'Usa --workspace-slug para acotar.'
                ))
                not_found_count += 1
                continue

            matches.first().delete()
            deleted_count += 1
            self.stdout.write(self.style.SUCCESS(f'Posición eliminada: {code}'))

        self.stdout.write(self.style.SUCCESS(
            f'Proceso completado. Eliminadas: {deleted_count}, No encontradas: {not_found_count}'))
