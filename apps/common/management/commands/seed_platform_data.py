"""
Datos globales de plataforma (compartidos entre owners).

Ejecutar una vez tras ``migrate`` en un entorno nuevo, antes de usar perfiles WMS:

    python manage.py seed_platform_data

Incluye:
- Categorías de producto y coexistencias (inventory)
- CustomPermission agrupados por módulo (core)
- PermissionMeta amigables para auth.Permission (core)
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.inventory.utils.default_categories import seed_default_categories


class Command(BaseCommand):
    help = "Carga datos globales WMS: categorías, permisos y metadatos de permisos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-categories",
            action="store_true",
            help="No sincronizar categorías/coexistencias.",
        )
        parser.add_argument(
            "--skip-permissions",
            action="store_true",
            help="No ejecutar create_permissions ni create_permission_meta.",
        )

    def handle(self, *args, **options):
        if not options["skip_categories"]:
            self.stdout.write("Sincronizando categorías globales…")
            seed_default_categories(stdout=self.stdout, style=self.style)

        if not options["skip_permissions"]:
            self.stdout.write("Sincronizando CustomPermission…")
            call_command("create_permissions", stdout=self.stdout)
            self.stdout.write("Sincronizando PermissionMeta…")
            call_command("create_permission_meta", stdout=self.stdout)

        self.stdout.write(self.style.SUCCESS("seed_platform_data completado."))
