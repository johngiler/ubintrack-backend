from django.db import models
from django.contrib.auth.models import AbstractUser, Permission

from apps.common.models import BaseModel
from apps.finance.models import Tariff

from . import choices
from . import services


class Profile(BaseModel):
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="profiles",
        verbose_name="workspace",
    )
    name = models.CharField(max_length=100, verbose_name="nombre del prefil")

    user_type = models.CharField(
        max_length=15,
        choices=choices.ProfileUserType.choices,
        blank=True,
        null=True,
        verbose_name="tipo de usuario",
    )

    permissions = models.ManyToManyField(
        "CustomPermission",
        blank=True,
        related_name="profiles",
        verbose_name="permisos del perfil",
    )

    class Meta:
        verbose_name = "perfil"
        verbose_name_plural = "perfiles"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                name="unique_profile_name_per_workspace",
            )
        ]

    def __str__(self):
        return self.name


class CustomUser(AbstractUser):
    groups = None
    user_permissions = None

    full_name = models.CharField(max_length=100, null=True,
                                 verbose_name='Nombre completo')

    user_type = models.CharField(
        max_length=15,
        choices=choices.UserType.choices,
        blank=True,
        null=True,
        verbose_name='tipo de usuario'
    )

    profile = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="custom_users",
        verbose_name="perfil del usuario",
        help_text="Perfil asociado (solo usuarios tipo Empleado y Cliente).",
    )

    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="staff_users",
        verbose_name="workspace",
        help_text="Owner al que pertenece (usuarios internos).",
    )

    client = models.ForeignKey(
        "Client",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="users",
        verbose_name="cliente",
        help_text="Cliente asociado (solo usuarios tipo Cliente).",
    )

    def get_all_permissions(self, obj=None):
        if self.is_superuser:
            return set()

        from apps.core.utils.user_roles import user_bypasses_permissions
        if user_bypasses_permissions(self):
            return set()

        perms = set()
        if self.profile:
            for custom_perm in self.profile.permissions.all():
                for perm in custom_perm.django_permissions.all():
                    perms.add(f"{perm.content_type.app_label}.{perm.codename}")

        return perms

    def has_perm(self, perm, obj=None):
        from apps.core.utils.user_roles import user_bypasses_permissions
        if user_bypasses_permissions(self):
            return True

        return perm in self.get_all_permissions(obj)

    def has_module_perms(self, app_label):
        from apps.core.utils.user_roles import user_bypasses_permissions
        if user_bypasses_permissions(self):
            return True

        for perm in self.get_all_permissions():
            if perm.startswith(f"{app_label}."):
                return True

        return False

    def get_full_name(self):
        """Nombre para UI: campo `full_name` del WMS (no first/last de AbstractUser)."""
        name = (self.full_name or "").strip()
        if name:
            return name
        first = (self.first_name or "").strip()
        last = (self.last_name or "").strip()
        return f"{first} {last}".strip()

    def get_short_name(self):
        name = self.get_full_name()
        if name:
            return name.split()[0]
        return (self.username or "").split("@", 1)[0] or self.username

    class Meta:
        verbose_name = 'usuario'
        verbose_name_plural = 'usuarios'

    def __str__(self):
        return self.get_full_name() or self.username


class CustomPermission(BaseModel):
    name = models.CharField(max_length=255, unique=True,
                            verbose_name='nombre del permiso')

    module = models.CharField(
        max_length=100,
        choices=[(app, app.capitalize())
                 for app in services.get_installed_apps()],
        blank=True,
        null=True,
        verbose_name='modulo'
    )

    django_permissions = models.ManyToManyField(
        Permission,
        blank=True,
        related_name='custom_permissions',
        verbose_name='permisos django'
    )

    class Meta:
        verbose_name = 'permiso'
        verbose_name_plural = "permisos"

    def __str__(self):
        return self.name


class PermissionMeta(BaseModel):
    permission = models.OneToOneField(
        Permission,
        on_delete=models.CASCADE,
        related_name='meta',
        verbose_name='permiso de django'
    )
    friendly_name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name='nombre amigable'
    )
    description = models.TextField(
        blank=True, null=True, verbose_name='descripción amigable')

    class Meta:
        verbose_name = 'metadato de permiso'
        verbose_name_plural = 'metadatos de permisos'

    def __str__(self):
        return self.friendly_name or self.permission.name


class Client(BaseModel):
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="clients",
        verbose_name="workspace",
    )
    name = models.CharField(max_length=200, null=True,
                            verbose_name="nombre del cliente")
    company = models.CharField(max_length=200, blank=True,
                               null=True, verbose_name='nombre de la compañía')
    address = models.TextField(blank=True, null=True, verbose_name='Dirección')
    ruc = models.CharField(max_length=200, blank=True,
                           null=True, verbose_name='RUC')
    dv = models.CharField(max_length=10, blank=True,
                          null=True, verbose_name='DV')
    random_id = models.CharField(
        max_length=15, editable=False, verbose_name="identificador único"
    )
    slug = models.SlugField(max_length=200, blank=True, null=True,
                            editable=False, verbose_name='apodo')

    tariff = models.ForeignKey(
        Tariff,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='tarifa'
    )

    cavas = models.ManyToManyField(
        'storage.Cava',
        blank=True,
        related_name='clients',
        verbose_name='selecionar cavas'
    )

    cold_positions = models.BigIntegerField(
        default=0,
        verbose_name="posiciones refrigeradas contratadas"
    )

    frozen_positions = models.BigIntegerField(
        default=0,
        verbose_name="posiciones congeladas contratadas"
    )

    def delete(self, *args, **kwargs):
        CustomUser.objects.filter(client=self).delete()
        super().delete(*args, **kwargs)

    def save(self, *args, **kwargs):
        if not self.random_id:
            self.random_id = services.generate_random_id()
        if not self.slug:
            self.slug = services.generate_slug(self.name, 9)
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "cliente"
        verbose_name_plural = "clientes"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "random_id"],
                name="unique_client_random_id_per_workspace",
            )
        ]

    def __str__(self):
        return self.name


class Branch(BaseModel):
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name='branches',
        verbose_name='cliente'
    )

    name = models.CharField(
        max_length=200, null=True, verbose_name='nombre de la sucursal')
    address = models.TextField(blank=True, null=True, verbose_name='dirección')
    longitude = models.CharField(max_length=200, blank=True,
                                 null=True, verbose_name='longitud')
    latitude = models.CharField(max_length=200, blank=True,
                                null=True, verbose_name='latitud')
    map_link = models.URLField(
        blank=True, null=True, verbose_name='enlace al mapa')

    class Meta:
        verbose_name = 'sucursal'
        verbose_name_plural = 'sucursales'

    def __str__(self):
        return self.name
