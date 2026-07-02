from __future__ import annotations

# Tablas legacy (pg_dump) → clave JSON en data/rbcold/import/parsed/
LEGACY_TABLES: dict[str, str] = {
    "tariffs": "finance_tariff",
    "storages": "storage_storage",
    "cavas": "storage_cava",
    "positions": "storage_position",
    "clients": "core_client",
    "client_cavas": "core_client_cavas",
    "branches": "core_branch",
    "profiles": "core_profile",
    "users": "core_customuser",
    "client_users": "core_clientuser",
    "products": "inventory_product",
    "entries": "order_entry",
    "entry_products": "order_entryproduct",
    "bins": "inventory_binentry",
    "bin_products": "inventory_binentryproduct",
    "exits": "order_exit",
    "exit_product_groups": "order_exitproductgroup",
    "exit_products": "order_exitproduct",
    "bin_movements": "inventory_binmovement",
    "exit_movements": "order_exitmovement",
}

# Solo lectura: mapear category_id legacy → category_code → Category global del seed
CATEGORY_LOOKUP_TABLE = "inventory_category"

DEFAULT_DUMP_PATH = "data/rbcold/db/rbcold_db_backup_20260701_161324.sql"
DEFAULT_PARSED_DIR = "data/rbcold/import/parsed"
DEFAULT_WORKSPACE_SLUG = "rbcold"

BULK_BATCH_SIZE = 500
