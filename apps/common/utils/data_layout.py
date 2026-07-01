"""
Rutas de datos locales (repo ``backend/data``), **aisladas por tenant** (slug del workspace).

No forma parte de ``MEDIA_ROOT``: es salida de comandos de gestión (p. ej. JSON intermedio del seed).
"""

from __future__ import annotations

from pathlib import Path


def backend_repo_root() -> Path:
    """Directorio del backend (donde está ``manage.py``)."""
    return Path(__file__).resolve().parents[3]


def safe_data_tenant_segment(slug: str | None) -> str:
    """Segmento de carpeta bajo ``data/``; coherente con sanitización de rutas de media."""
    s = (str(slug).strip().lower() if slug else "") or "unknown"
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in s)
    safe = safe.strip("-") or "unknown"
    return safe[:80]


def tenant_data_dir(workspace_slug: str, *relative_parts: str) -> Path:
    """
    Directorio bajo ``data/<workspace_slug>/…`` para salidas de comandos por tenant.

    Ejemplo::

        tenant_data_dir("rbcold", "imports", "pending") → data/rbcold/imports/pending/
    """
    seg = safe_data_tenant_segment(workspace_slug)
    return (backend_repo_root() / "data" / seg / Path(*relative_parts)).resolve()
