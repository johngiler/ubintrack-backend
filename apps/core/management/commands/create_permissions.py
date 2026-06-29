from django.core.management.base import BaseCommand
from apps.core.models import CustomPermission
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.models import Permission
from apps.inventory.models import Bin, BinProduct, Category, Product
from apps.order.models import Entry, EntryDetail, EntryProduct, Exit, ExitDetail, ExitProduct
from apps.storage.models import Cava, Position, Storage
from apps.task.models import Task, TaskLog

# Definir los tipos de permisos personalizados
PERMISSION_TYPES = {
    "Consultar": ["view"],
    "Cambiar": ["view", "change"],
    "": ["view", "change", "add", "delete"],
}

# Modelos organizados por aplicación
MODELS = {
    "inventory": [Bin, BinProduct, Category, Product],
    "order": [Entry, EntryDetail, EntryProduct, Exit, ExitDetail, ExitProduct],
    "storage": [Cava, Position, Storage],
    "task": [Task, TaskLog],
}

# Relación de modelos principales con sus modelos relacionados
RELATED_MODELS = {
    Entry: [EntryDetail, EntryProduct],
    Exit: [ExitDetail, ExitProduct],
    Bin: [BinProduct],
}


class Command(BaseCommand):
    help = "Create custom permissions for inventory, order, storage, and task apps"

    def handle(self, *args, **kwargs):
        for app_label, models in MODELS.items():
            for model in models:
                content_type = ContentType.objects.get_for_model(model)
                for perm_name, actions in PERMISSION_TYPES.items():
                    # Generar el nombre del permiso personalizado, en formato UpperCamelCase
                    custom_perm_name = f"{perm_name} {
                        model._meta.verbose_name}".strip().title()

                    # Obtener los permisos de Django relevantes para el modelo principal
                    django_perms = Permission.objects.filter(
                        content_type=content_type,
                        codename__in=[
                            f"{action}_{model._meta.model_name}" for action in actions
                        ],
                    )

                    # Incluir permisos de modelos relacionados si existen
                    related_models = RELATED_MODELS.get(model, [])
                    for related_model in related_models:
                        related_content_type = ContentType.objects.get_for_model(
                            related_model)
                        related_perms = Permission.objects.filter(
                            content_type=related_content_type,
                            codename__in=[
                                f"{action}_{related_model._meta.model_name}" for action in actions
                            ],
                        )
                        django_perms = django_perms | related_perms

                    # Crear o asociar el permiso personalizado en CustomPermission
                    custom_perm, created = CustomPermission.objects.get_or_create(
                        name=custom_perm_name,
                        defaults={"module": app_label},
                    )

                    # Asociar permisos de Django con CustomPermission
                    custom_perm.django_permissions.set(django_perms)
                    custom_perm.module = app_label  # Asociar el módulo
                    custom_perm.save()

                    if created:
                        self.stdout.write(self.style.SUCCESS(
                            f"CustomPermission '{custom_perm_name}' created."))
                    else:
                        self.stdout.write(self.style.WARNING(
                            f"CustomPermission '{custom_perm_name}' already exists."))
