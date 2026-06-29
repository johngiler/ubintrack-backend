"""Acceso al catálogo público de tomas (según modelo ShoppingCenter)."""


def shopping_center_allows_public_catalog(center) -> bool:
    """Centro activo: catálogo público y reservas en marketplace."""
    return bool(getattr(center, "is_active", False))
