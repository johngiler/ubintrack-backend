"""
Tenant lógico del SaaS WMS: un owner (empresa operadora) con aislamiento de datos.

- Subdominio: `slug` → `https://{slug}.ubintrack.com`
- Branding: logos, colores, textos del WMS por owner.
"""

from django.db import models

from apps.common.models import TimeStampedActiveModel
from apps.common.utils.media_layout import (
    workspace_brand_favicon_upload,
    workspace_brand_logo_mark_upload,
    workspace_brand_logo_upload,
)
from apps.workspaces.utils.validators import (
    validate_brand_graphic,
    validate_favicon_file,
)


class Workspace(TimeStampedActiveModel):
    slug = models.SlugField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Identificador para subdominio (solo minúsculas, números, guiones).",
    )
    name = models.CharField(
        max_length=200,
        help_text="Nombre comercial del owner (operador del WMS).",
    )
    legal_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Razón social (opcional).",
    )
    rif = models.CharField("RIF", max_length=32, blank=True)
    logo = models.FileField(
        "Logo",
        upload_to=workspace_brand_logo_upload,
        blank=True,
        null=True,
        validators=[validate_brand_graphic],
    )
    logo_mark = models.FileField(
        "Isotipo",
        upload_to=workspace_brand_logo_mark_upload,
        blank=True,
        null=True,
        validators=[validate_brand_graphic],
    )
    favicon = models.FileField(
        "Favicon",
        upload_to=workspace_brand_favicon_upload,
        blank=True,
        null=True,
        validators=[validate_favicon_file],
    )
    primary_color = models.CharField("Color primario", max_length=32, blank=True)
    secondary_color = models.CharField("Color secundario", max_length=32, blank=True)
    support_email = models.EmailField("Correo de soporte", blank=True)
    phone = models.CharField("Teléfono", max_length=32, blank=True)
    app_title = models.CharField(
        "Título del WMS",
        max_length=120,
        blank=True,
        help_text="Nombre corto que ve el usuario (si vacío, se usa el nombre del workspace).",
    )
    app_tagline = models.CharField(
        "Eslogan",
        max_length=255,
        blank=True,
    )
    invoice_prefix = models.CharField(
        "Prefijo de factura",
        max_length=20,
        blank=True,
        default="",
        help_text="Prefijo para comprobantes (ej. RBCOLD).",
    )

    class Meta:
        ordering = ["slug"]
        verbose_name = "Workspace (owner)"
        verbose_name_plural = "Workspaces (owners)"

    def __str__(self):
        return f"{self.slug} — {self.name}"
