"""Validadores de archivos de branding (SVG + mapas de bits)."""

from django.core.validators import FileExtensionValidator

# Logos / isotipo: raster + vector. ImageField de Django usa Pillow y rechaza SVG.
BRAND_GRAPHIC_EXTENSIONS = ("svg", "png", "jpg", "jpeg", "gif", "webp")
FAVICON_EXTENSIONS = BRAND_GRAPHIC_EXTENSIONS + ("ico",)

PNG_ARTIFACTS_EXTENSIONS = ("png",)

validate_brand_graphic = FileExtensionValidator(
    allowed_extensions=BRAND_GRAPHIC_EXTENSIONS,
    message="Formato no admitido. Usa SVG, PNG, JPEG, GIF o WebP.",
)

validate_favicon_file = FileExtensionValidator(
    allowed_extensions=FAVICON_EXTENSIONS,
    message="Formato no admitido. Usa SVG, PNG, ICO, JPEG, GIF o WebP.",
)

validate_png_artifacts = FileExtensionValidator(
    allowed_extensions=PNG_ARTIFACTS_EXTENSIONS,
    message="Formato no admitido. Usa solo PNG.",
)
