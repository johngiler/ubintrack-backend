from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.conf import settings

from apps.common.legacy_import.constants import (
    CATEGORY_LOOKUP_TABLE,
    DEFAULT_DUMP_PATH,
    DEFAULT_PARSED_DIR,
    LEGACY_TABLES,
)
from apps.common.legacy_import.pg_copy import extract_table_rows


def resolve_dump_path(dump_path: str | None) -> Path:
    base = Path(settings.BASE_DIR)
    path = Path(dump_path) if dump_path else base / DEFAULT_DUMP_PATH
    if not path.is_absolute():
        path = base / path
    if not path.exists():
        raise FileNotFoundError(f"No existe el dump legacy: {path}")
    return path


def resolve_parsed_dir(parsed_dir: str | None) -> Path:
    base = Path(settings.BASE_DIR)
    path = Path(parsed_dir) if parsed_dir else base / DEFAULT_PARSED_DIR
    if not path.is_absolute():
        path = base / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def extract_legacy_dump(
    *,
    dump_path: str | None = None,
    parsed_dir: str | None = None,
) -> dict[str, Any]:
    dump = resolve_dump_path(dump_path)
    out_dir = resolve_parsed_dir(parsed_dir)

    manifest: dict[str, Any] = {
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "source_dump": str(dump),
        "workspace_slug": "rbcold",
        "tables": {},
        "notes": [
            "Datos operativos legacy single-tenant → JSON intermedio para import_legacy_rbcold.",
            "No incluye django_migrations, permisos globales ni categorías (usa seed_platform_data).",
            "Incluye historial: inventory_binmovement y order_exitmovement.",
            "Sucursales (branches) incluidas porque las salidas referencian branch_id.",
        ],
    }

    for json_key, table_name in LEGACY_TABLES.items():
        rows = extract_table_rows(dump, table_name)
        payload = rows
        target = out_dir / f"{json_key}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["tables"][json_key] = {
            "legacy_table": table_name,
            "rows": len(rows),
            "file": target.name,
        }

    categories = extract_table_rows(dump, CATEGORY_LOOKUP_TABLE)
    cat_lookup = {
        str(row["id"]): row["category_code"]
        for row in categories
        if row.get("id") is not None and row.get("category_code")
    }
    lookup_file = out_dir / "category_id_to_code.json"
    lookup_file.write_text(json.dumps(cat_lookup, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["tables"]["category_id_to_code"] = {
        "legacy_table": CATEGORY_LOOKUP_TABLE,
        "rows": len(cat_lookup),
        "file": lookup_file.name,
        "purpose": "lookup_only",
    }

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
