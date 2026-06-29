from django.core.management.base import BaseCommand

from apps.workspaces.models import Workspace
from apps.workspaces.tenant import default_workspace_slug


class Command(BaseCommand):
    help = "Crea el workspace por defecto si no existe (desarrollo / primer deploy)."

    def add_arguments(self, parser):
        parser.add_argument("--slug", default=None, help="Slug del workspace")
        parser.add_argument("--name", default=None, help="Nombre comercial")

    def handle(self, *args, **options):
        slug = (options["slug"] or default_workspace_slug()).strip().lower()
        name = options["name"] or slug.upper()
        ws, created = Workspace.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "app_title": name, "invoice_prefix": slug.upper()[:10]},
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Workspace creado: {ws.slug}"))
        else:
            self.stdout.write(f"Workspace ya existe: {ws.slug}")
