"""
Tenant lógico del SaaS WMS: un owner (empresa operadora) con aislamiento de datos.

- Subdominio: `slug` → `https://{slug}.ubintrack.com`
- Branding: logos, colores, textos del WMS por owner.
"""

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.common.models import BaseModel
from apps.common.utils.media_layout import (
    workspace_brand_favicon_upload,
    workspace_brand_logo_mark_upload,
    workspace_brand_logo_upload,
)
from apps.workspaces.utils.validators import (
    validate_brand_graphic,
    validate_favicon_file,
)


class Workspace(BaseModel):
    class SidebarLogoGradientMode(models.TextChoices):
        NONE = "none", "Desactivado"
        LIGHT = "light", "Solo modo claro"
        DARK = "dark", "Solo modo oscuro"
        BOTH = "both", "Ambos modos"

    class PrinterLanguage(models.TextChoices):
        ZPL = "zpl", "ZPL"

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
    sidebar_logo_gradient_mode = models.CharField(
        "Fondo gradiente en logo del sidebar",
        max_length=8,
        choices=SidebarLogoGradientMode.choices,
        default=SidebarLogoGradientMode.NONE,
        help_text="Aplica un fondo con los colores de marca detrás del logo en el menú lateral.",
    )
    invoice_prefix = models.CharField(
        "Prefijo de factura",
        max_length=20,
        blank=True,
        default="",
        help_text="Prefijo para comprobantes (ej. RBCOLD).",
    )
    printer_ip = models.CharField(
        "IP o host de impresora",
        max_length=255,
        blank=True,
        default="",
        help_text="Impresora de red del owner. Si vacío, se usa el fallback del entorno.",
    )
    printer_port = models.PositiveIntegerField(
        "Puerto de impresora",
        default=9100,
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
    )
    label_width_in = models.DecimalField(
        "Ancho de etiqueta (pulgadas)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("4.00"),
        validators=[MinValueValidator(Decimal("0.50")), MaxValueValidator(Decimal("12.00"))],
    )
    label_height_in = models.DecimalField(
        "Alto de etiqueta (pulgadas)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("2.00"),
        validators=[MinValueValidator(Decimal("0.50")), MaxValueValidator(Decimal("12.00"))],
    )
    printer_dpi = models.PositiveIntegerField(
        "DPI de impresora",
        default=203,
        validators=[MinValueValidator(72), MaxValueValidator(600)],
    )
    printer_language = models.CharField(
        "Lenguaje de impresora",
        max_length=16,
        choices=PrinterLanguage.choices,
        default=PrinterLanguage.ZPL,
    )

    class Meta:
        ordering = ["slug"]
        verbose_name = "Workspace (owner)"
        verbose_name_plural = "Workspaces (owners)"

    def __str__(self):
        return f"{self.slug} — {self.name}"
