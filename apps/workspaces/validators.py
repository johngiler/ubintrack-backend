"""Compat: migraciones y modelos importan ``apps.workspaces.validators``."""

from apps.workspaces.utils.workspace_validators import (
    BRAND_GRAPHIC_EXTENSIONS,
    FAVICON_EXTENSIONS,
    PNG_ARTIFACTS_EXTENSIONS,
    validate_brand_graphic,
    validate_favicon_file,
    validate_png_artifacts,
)

__all__ = [
    "BRAND_GRAPHIC_EXTENSIONS",
    "FAVICON_EXTENSIONS",
    "PNG_ARTIFACTS_EXTENSIONS",
    "validate_brand_graphic",
    "validate_favicon_file",
    "validate_png_artifacts",
]
