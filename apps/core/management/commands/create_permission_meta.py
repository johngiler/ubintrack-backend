from django.core.management.base import BaseCommand
from django.contrib.auth.models import Permission
from django.apps import apps

from apps.core.models import PermissionMeta


VERBOS = {
    'add': 'Crear',
    'change': 'Editar',
    'delete': 'Eliminar',
    'view': 'Listar',
}


def get_model_names(app_label: str, model_name: str):
    try:
        model = apps.get_model(app_label, model_name)
        singular = str(model._meta.verbose_name or model_name).strip()
        plural = str(model._meta.verbose_name_plural or f"{singular}s").strip()
    except Exception:
        singular = model_name.replace('_', ' ').title()
        plural = f"{singular}s"
    return singular, plural


class Command(BaseCommand):
    help = (
        "Crea o actualiza metadatos amigables (friendly_name, description) "
        "para todos los auth.Permission."
    )

    def handle(self, *args, **options):
        created = 0
        updated = 0

        perms = Permission.objects.select_related('content_type').all()
        for perm in perms:
            app_label = perm.content_type.app_label
            model_name = perm.content_type.model
            action = perm.codename.split('_', 1)[0]
            singular, plural = get_model_names(app_label, model_name)

            verbo = VERBOS.get(action, perm.name.split(' ', 1)[0].title())
            # Usamos plural en el friendly, coherente con autorización general
            friendly_name = f"{verbo} {plural.title()}"

            # Descripción clara y corta
            if action in VERBOS:
                description = f"Permite {verbo.lower()} {plural.lower()}"
            else:
                description = f"Permite {perm.name.lower()}"

            obj, is_created = PermissionMeta.objects.update_or_create(
                permission=perm,
                defaults={
                    'friendly_name': friendly_name,
                    'description': description,
                    'is_active': True,
                },
            )
            if is_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"PermissionMeta sincronizado. Creados: {created}, Actualizados: {updated}."
            )
        )
