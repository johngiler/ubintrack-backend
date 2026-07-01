"""Validadores de archivos de branding del owner (SVG + mapas de bits)."""

from django.core.validators import FileExtensionValidator

BRAND_GRAPHIC_EXTENSIONS = ("svg", "png", "jpg", "jpeg", "gif", "webp")
FAVICON_EXTENSIONS = BRAND_GRAPHIC_EXTENSIONS + ("ico",)

validate_brand_graphic = FileExtensionValidator(
    allowed_extensions=BRAND_GRAPHIC_EXTENSIONS,
    message="Formato no admitido. Usa SVG, PNG, JPEG, GIF o WebP.",
)

validate_favicon_file = FileExtensionValidator(
    allowed_extensions=FAVICON_EXTENSIONS,
    message="Formato no admitido. Usa SVG, PNG, ICO, JPEG, GIF o WebP.",
)
