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


def catalog_seed_json_path(workspace_slug: str) -> Path:
    """
    JSON normalizado del comando ``seed_production_catalog`` (se sobrescribe en cada corrida).

    Estructura::

        data/<workspace_slug>/catalog/data.json
    """
    seg = safe_data_tenant_segment(workspace_slug)
    return (backend_repo_root() / "data" / seg / "catalog" / "data.json").resolve()
