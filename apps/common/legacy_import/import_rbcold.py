from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.apps import apps
from django.conf import settings
from django.db import connection, transaction
from django.utils.dateparse import parse_date, parse_datetime

from apps.common.legacy_import.constants import (
    BULK_BATCH_SIZE,
    DEFAULT_PARSED_DIR,
    DEFAULT_WORKSPACE_SLUG,
    LEGACY_TABLES,
)
from apps.common.legacy_import.extract import resolve_parsed_dir
from apps.core import choices as core_choices
from apps.core.models import Client, CustomUser, Profile
from apps.finance.models import Tariff
from apps.inventory.models import Bin, BinMovement, BinProduct, Category, Product
from apps.order.models import (
    Entry,
    EntryProduct,
    Exit,
    ExitMovement,
    ExitProduct,
    ExitProductGroup,
)
from apps.storage.models import Cava, Position, Storage
from apps.workspaces.models import Workspace


def _load_json(parsed_dir: Path, name: str) -> list[dict[str, Any]] | dict[str, Any]:
    path = parsed_dir / name
    if not path.exists():
        raise FileNotFoundError(f"Falta archivo parseado: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_bool(value: Any) -> bool:
    if value is None:
        return False
    return str(value).lower() in {"t", "true", "1", "yes"}


def _parse_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    return int(value)


def _parse_decimal(value: Any, default: Decimal | str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    return Decimal(str(value))


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        raise ValueError(f"datetime inválido: {value!r}")
    return parsed


def _parse_d(value: Any) -> date | None:
    if value is None or value == "":
        return None
    parsed = parse_date(str(value))
    if parsed is None:
        raise ValueError(f"date inválido: {value!r}")
    return parsed


def _parse_json(value: Any) -> dict | list | None:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(str(value))


def _map_id(id_maps: dict[str, dict[str, int]], bucket: str, legacy_id: Any) -> int | None:
    if legacy_id is None or legacy_id == "":
        return None
    return id_maps[bucket].get(str(legacy_id))


class LegacyRbcoldImporter:
    def __init__(
        self,
        *,
        workspace_slug: str = DEFAULT_WORKSPACE_SLUG,
        parsed_dir: str | None = None,
        dry_run: bool = False,
        clear_workspace: bool = False,
    ):
        self.workspace_slug = workspace_slug.strip().lower()
        self.parsed_dir = resolve_parsed_dir(parsed_dir)
        self.dry_run = dry_run
        self.clear_workspace = clear_workspace
        self.id_maps: dict[str, dict[str, int]] = {
            key: {} for key in LEGACY_TABLES}
        self.stats: dict[str, int] = {}
        self.warnings: list[str] = []

    def _load_rows(self, key: str) -> list[dict[str, Any]]:
        return _load_json(self.parsed_dir, f"{key}.json")

    def _bump(self, key: str, count: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + count

    def _require_workspace(self) -> Workspace:
        try:
            return Workspace.objects.get(slug=self.workspace_slug)
        except Workspace.DoesNotExist as exc:
            raise RuntimeError(
                f"Workspace '{self.workspace_slug}' no existe. "
                "Ejecuta: python manage.py ensure_default_workspace --slug rbcold"
            ) from exc

    def preview(self) -> dict[str, Any]:
        manifest_path = self.parsed_dir / "manifest.json"
        if manifest_path.exists():
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        counts = {key: len(self._load_rows(key)) for key in LEGACY_TABLES}
        return {"tables": counts}

    def _assert_workspace_empty(self, workspace: Workspace) -> None:
        blockers = {
            "clients": Client.objects.filter(workspace=workspace).count(),
            "storages": Storage.objects.filter(workspace=workspace).count(),
            "tariffs": Tariff.objects.filter(workspace=workspace).count(),
        }
        active = {k: v for k, v in blockers.items() if v > 0}
        if active:
            raise RuntimeError(
                f"El workspace '{workspace.slug}' ya tiene datos: {active}. "
                "Usa --clear-workspace --confirm para reemplazarlos."
            )

    def _clear_workspace_data(self, workspace: Workspace) -> None:
        if self.dry_run:
            self.warnings.append(
                "[dry-run] Se omitió borrado de datos del workspace.")
            return

        ExitProduct.objects.filter(
            exit_product_group__exit__client__workspace=workspace
        ).delete()
        ExitMovement.objects.filter(exit__client__workspace=workspace).delete()
        ExitProductGroup.objects.filter(
            exit__client__workspace=workspace).delete()
        Exit.objects.filter(client__workspace=workspace).delete()
        BinMovement.objects.filter(bin__client__workspace=workspace).delete()
        BinProduct.objects.filter(bin__client__workspace=workspace).delete()
        Bin.objects.filter(client__workspace=workspace).delete()
        EntryProduct.objects.filter(
            entry__client__workspace=workspace).delete()
        Entry.objects.filter(client__workspace=workspace).delete()
        Product.objects.filter(client__workspace=workspace).delete()
        Client.objects.filter(workspace=workspace).delete()
        Position.objects.filter(cava__storage__workspace=workspace).delete()
        Cava.objects.filter(storage__workspace=workspace).delete()
        Storage.objects.filter(workspace=workspace).delete()
        Tariff.objects.filter(workspace=workspace).delete()
        Profile.objects.filter(workspace=workspace).delete()
        CustomUser.objects.filter(workspace=workspace).delete()

    def _build_category_map(self) -> dict[str, int]:
        lookup = _load_json(self.parsed_dir, "category_id_to_code.json")
        categories = Category.objects.in_bulk(field_name="category_code")
        mapping: dict[str, int] = {}
        for legacy_id, code in lookup.items():
            category = categories.get(code)
            if category:
                mapping[str(legacy_id)] = category.pk
            else:
                self.warnings.append(
                    f"Categoría legacy id={legacy_id} code={code!r} no existe en seed; productos quedarán sin categoría."
                )
        return mapping

    def _bulk_insert(self, model, key: str, objects: list[Any], legacy_ids: list[str]) -> None:
        if not objects:
            return
        if self.dry_run:
            self._bump(key, len(objects))
            for legacy_id in legacy_ids:
                # IDs sintéticos para validar FKs en simulación (rollback al final).
                self.id_maps[key][legacy_id] = int(legacy_id)
            return
        for start in range(0, len(objects), BULK_BATCH_SIZE):
            chunk = objects[start: start + BULK_BATCH_SIZE]
            chunk_ids = legacy_ids[start: start + BULK_BATCH_SIZE]
            created = model.objects.bulk_create(
                chunk, batch_size=BULK_BATCH_SIZE)
            for legacy_id, obj in zip(chunk_ids, created):
                self.id_maps[key][legacy_id] = obj.pk
            self._bump(key, len(chunk))

    def import_all(self) -> dict[str, Any]:
        workspace = self._require_workspace()
        if self.clear_workspace:
            self._clear_workspace_data(workspace)
        elif not self.dry_run:
            self._assert_workspace_empty(workspace)

        category_map = self._build_category_map()

        with transaction.atomic():
            self._import_tariffs(workspace)
            self._import_storages(workspace)
            self._import_cavas()
            self._import_positions(workspace)
            self._import_clients(workspace)
            self._import_branches()
            self._import_profiles(workspace)
            self._import_users(workspace)
            self._import_client_users(workspace)
            self._import_client_cavas()
            self._import_products(category_map)
            self._import_entries()
            self._import_entry_products()
            self._import_bins(workspace)
            self._import_bin_products()
            self._import_exits()
            self._import_exit_product_groups()
            self._import_exit_products()
            self._import_bin_movements()
            self._import_exit_movements()

            if self.dry_run:
                transaction.set_rollback(True)

        if not self.dry_run:
            self._reset_sequences()
            maps_path = self.parsed_dir / "id_maps.json"
            maps_path.write_text(
                json.dumps(self.id_maps, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        return {
            "workspace": workspace.slug,
            "dry_run": self.dry_run,
            "stats": self.stats,
            "warnings": self.warnings,
            "id_maps_file": None if self.dry_run else str(self.parsed_dir / "id_maps.json"),
        }

    def _import_tariffs(self, workspace: Workspace) -> None:
        rows = self._load_rows("tariffs")
        objects = []
        legacy_ids = []
        for row in rows:
            objects.append(
                Tariff(
                    workspace=workspace,
                    name=row["name"],
                    in_pallet_frozen=_parse_decimal(
                        row.get("in_pallet_frozen")),
                    in_floor_frozen=_parse_decimal(row.get("in_floor_frozen")),
                    out_frozen=_parse_decimal(row.get("out_frozen")),
                    in_pallet_refrigerated=_parse_decimal(
                        row.get("in_pallet_refrigerated")),
                    in_floor_refrigerated=_parse_decimal(
                        row.get("in_floor_refrigerated")),
                    out_refrigerated=_parse_decimal(
                        row.get("out_refrigerated")),
                    in_pallet_dry=_parse_decimal(row.get("in_pallet_dry")),
                    in_floor_dry=_parse_decimal(row.get("in_floor_dry")),
                    out_dry=_parse_decimal(row.get("out_dry")),
                    refrigerated_storage=_parse_decimal(
                        row.get("refrigerated_storage")),
                    frozen_storage=_parse_decimal(row.get("frozen_storage")),
                    dry_storage=_parse_decimal(row.get("dry_storage")),
                    picking=_parse_decimal(row.get("picking")),
                    has_picking_tariff=_parse_bool(
                        row.get("has_picking_tariff")),
                    restiba=_parse_decimal(row.get("restiba")),
                    added_pallet=_parse_decimal(row.get("added_pallet")),
                    wrap=_parse_decimal(row.get("wrap")),
                    rotation_threshold=_parse_decimal(
                        row.get("rotation_threshold"), "0.40"),
                    static_penalty_rate=_parse_decimal(
                        row.get("static_penalty_rate"), "0.80"),
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        self._bulk_insert(Tariff, "tariffs", objects, legacy_ids)

    def _import_storages(self, workspace: Workspace) -> None:
        rows = self._load_rows("storages")
        objects = []
        legacy_ids = []
        for row in rows:
            objects.append(
                Storage(
                    workspace=workspace,
                    is_active=_parse_bool(row.get("is_active", True)),
                    name=row["name"],
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        self._bulk_insert(Storage, "storages", objects, legacy_ids)

    def _import_cavas(self) -> None:
        rows = self._load_rows("cavas")
        objects = []
        legacy_ids = []
        for row in rows:
            storage_id = _map_id(self.id_maps, "storages",
                                 row.get("storage_id"))
            objects.append(
                Cava(
                    storage_id=storage_id,
                    is_active=_parse_bool(row.get("is_active", True)),
                    name=row["name"],
                    condition=row["condition"],
                    current_temp=_parse_int(row.get("current_temp")),
                    temp_min=_parse_int(row.get("temp_min")),
                    temp_max=_parse_int(row.get("temp_max")),
                    total_levels=_parse_int(row.get("total_levels"), 0) or 0,
                    positions_has_been_generated=_parse_bool(
                        row.get("positions_has_been_generated")),
                    is_virtual=_parse_bool(row.get("is_virtual")),
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        self._bulk_insert(Cava, "cavas", objects, legacy_ids)

    def _import_positions(self, workspace: Workspace) -> None:
        rows = self._load_rows("positions")
        objects = []
        legacy_ids = []
        for row in rows:
            cava_id = _map_id(self.id_maps, "cavas", row.get("cava_id"))
            objects.append(
                Position(
                    cava_id=cava_id,
                    workspace=workspace,
                    is_active=_parse_bool(row.get("is_active", True)),
                    status=row["status"],
                    level=_parse_int(row.get("level"), 1) or 1,
                    place=row["place"],
                    rack=_parse_int(row.get("rack"), 1) or 1,
                    hall=row["hall"],
                    is_picking=_parse_bool(row.get("is_picking")),
                    code=row["code"],
                    zpl_code=row.get("zpl_code"),
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        self._bulk_insert(Position, "positions", objects, legacy_ids)

    def _import_clients(self, workspace: Workspace) -> None:
        rows = self._load_rows("clients")
        objects = []
        legacy_ids = []
        for row in rows:
            tariff_id = _map_id(self.id_maps, "tariffs", row.get("tariff_id"))
            objects.append(
                Client(
                    workspace=workspace,
                    is_active=_parse_bool(row.get("is_active", True)),
                    name=row.get("name"),
                    company=row.get("company"),
                    address=row.get("address"),
                    ruc=row.get("ruc"),
                    dv=row.get("dv"),
                    random_id=row["random_id"],
                    slug=row.get("slug"),
                    tariff_id=tariff_id,
                    cold_positions=_parse_int(
                        row.get("cold_positions"), 0) or 0,
                    frozen_positions=_parse_int(
                        row.get("frozen_positions"), 0) or 0,
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        self._bulk_insert(Client, "clients", objects, legacy_ids)

    def _import_branches(self) -> None:
        Branch = apps.get_model("core", "Branch")
        rows = self._load_rows("branches")
        objects = []
        legacy_ids = []
        for row in rows:
            client_id = _map_id(self.id_maps, "clients", row.get("client_id"))
            if not client_id:
                self.warnings.append(
                    f"Sucursal legacy id={row.get('id')} sin client_id mapeado; omitida.")
                continue
            objects.append(
                Branch(
                    client_id=client_id,
                    is_active=_parse_bool(row.get("is_active", True)),
                    name=row.get("name"),
                    address=row.get("address"),
                    longitude=row.get("longitude"),
                    latitude=row.get("latitude"),
                    map_link=row.get("map_link") or None,
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        self._bulk_insert(Branch, "branches", objects, legacy_ids)

    def _import_profiles(self, workspace: Workspace) -> None:
        rows = self._load_rows("profiles")
        objects = []
        legacy_ids = []
        for row in rows:
            name = row["name"]
            user_type = None
            lowered = (name or "").strip().lower()
            if lowered == "cliente":
                user_type = core_choices.ProfileUserType.CLIENT
            elif lowered.startswith("admin"):
                user_type = core_choices.ProfileUserType.EMPLOYEE
            else:
                user_type = core_choices.ProfileUserType.EMPLOYEE
            objects.append(
                Profile(
                    workspace=workspace,
                    is_active=_parse_bool(row.get("is_active", True)),
                    name=name,
                    user_type=user_type,
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        self._bulk_insert(Profile, "profiles", objects, legacy_ids)

    def _import_users(self, workspace: Workspace) -> None:
        rows = self._load_rows("users")
        if self.dry_run:
            self._bump("users", len(rows))
            for row in rows:
                self.id_maps["users"][str(row["id"])] = int(row["id"])
            return

        for row in rows:
            profile_id = _map_id(self.id_maps, "profiles",
                                 row.get("profile_id"))
            user_type = row.get("user_type") or core_choices.UserType.EMPLOYEE
            user = CustomUser(
                password=row["password"],
                last_login=_parse_dt(row.get("last_login")),
                is_superuser=_parse_bool(row.get("is_superuser")),
                username=row["username"],
                first_name=row.get("first_name") or "",
                last_name=row.get("last_name") or "",
                email=row.get("email") or "",
                is_staff=_parse_bool(row.get("is_staff")),
                is_active=_parse_bool(row.get("is_active", True)),
                date_joined=_parse_dt(row.get("date_joined")),
                full_name=row.get("full_name"),
                user_type=user_type,
                profile_id=profile_id,
                workspace=workspace,
            )
            user.save()
            self.id_maps["users"][str(row["id"])] = user.pk
            self._bump("users")

    def _import_client_users(self, workspace: Workspace) -> None:
        rows = self._load_rows("client_users")
        if self.dry_run:
            self._bump("client_users", len(rows))
            return

        for row in rows:
            client_id = _map_id(self.id_maps, "clients", row.get("client_id"))
            if not client_id:
                self.warnings.append(
                    f"client_user legacy id={row.get('id')} sin client_id; omitido.")
                continue

            legacy_user_id = row.get("user_id")
            if legacy_user_id:
                user_pk = _map_id(self.id_maps, "users", legacy_user_id)
                if not user_pk:
                    self.warnings.append(
                        f"client_user legacy id={row.get('id')} referencia user_id={legacy_user_id} inexistente."
                    )
                    continue
                CustomUser.objects.filter(pk=user_pk).update(
                    client_id=client_id,
                    user_type=core_choices.UserType.CLIENT,
                    workspace=workspace,
                )
                self._bump("client_users")
                continue

            email = (row.get("email") or "").strip()
            username = email or f"client_{client_id}_{row['id']}"
            password = row.get("plain_password") or "changeme"
            user = CustomUser.objects.create_user(
                username=username,
                email=email or f"{username}@import.local",
                password=password,
                full_name=row.get("full_name"),
                user_type=core_choices.UserType.CLIENT,
                client_id=client_id,
                workspace=workspace,
                is_active=_parse_bool(row.get("is_active", True)),
            )
            self.id_maps.setdefault("client_users", {})[
                str(row["id"])] = user.pk
            self._bump("client_users")

    def _import_client_cavas(self) -> None:
        rows = self._load_rows("client_cavas")
        if self.dry_run:
            self._bump("client_cavas", len(rows))
            return
        for row in rows:
            client_id = _map_id(self.id_maps, "clients", row.get("client_id"))
            cava_id = _map_id(self.id_maps, "cavas", row.get("cava_id"))
            if client_id and cava_id:
                Client.objects.get(pk=client_id).cavas.add(cava_id)
        self._bump("client_cavas", len(rows))

    def _import_products(self, category_map: dict[str, int]) -> None:
        rows = self._load_rows("products")
        objects = []
        legacy_ids = []
        for row in rows:
            client_id = _map_id(self.id_maps, "clients", row.get("client_id"))
            category_id = category_map.get(
                str(row.get("category_id"))) if row.get("category_id") else None
            objects.append(
                Product(
                    client_id=client_id,
                    is_active=_parse_bool(row.get("is_active", True)),
                    name=row["name"],
                    code=row["code"],
                    client_sku=row.get("client_sku"),
                    rotation_type=row["rotation_type"],
                    uom=row["uom"],
                    kg_uom=_parse_decimal(row.get("kg_uom")),
                    units_in_uom=_parse_int(row.get("units_in_uom"), 0) or 0,
                    qty_uom_pallet=_parse_int(
                        row.get("qty_uom_pallet"), 0) or 0,
                    condition=row.get("condition"),
                    storage_temperature=_parse_decimal(row.get("storage_temperature")) if row.get(
                        "storage_temperature") not in (None, "") else None,
                    category_id=category_id,
                    supplier=row.get("supplier"),
                    comment=row.get("comment") or "",
                    is_picking=_parse_bool(row.get("is_picking")),
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        self._bulk_insert(Product, "products", objects, legacy_ids)

    def _import_entries(self) -> None:
        rows = self._load_rows("entries")
        objects = []
        legacy_ids = []
        for row in rows:
            objects.append(
                Entry(
                    client_id=_map_id(self.id_maps, "clients",
                                      row.get("client_id")),
                    storage_id=_map_id(
                        self.id_maps, "storages", row.get("storage_id")),
                    status=row.get("status"),
                    condition=row.get("condition"),
                    in_type=row.get("in_type"),
                    code=row["code"],
                    entry_date=_parse_d(row.get("entry_date")),
                    total_restibas=_parse_int(
                        row.get("total_restibas"), 0) or 0,
                    total_wraps=_parse_int(row.get("total_wraps"), 0) or 0,
                    total_added_pallets=_parse_int(
                        row.get("total_added_pallets"), 0) or 0,
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        self._bulk_insert(Entry, "entries", objects, legacy_ids)

    def _import_entry_products(self) -> None:
        rows = self._load_rows("entry_products")
        objects = []
        legacy_ids = []
        for row in rows:
            objects.append(
                EntryProduct(
                    entry_id=_map_id(self.id_maps, "entries",
                                     row.get("entry_id")),
                    product_id=_map_id(
                        self.id_maps, "products", row.get("product_id")),
                    quantity=_parse_int(row.get("quantity"), 0) or 0,
                    real_quantity=_parse_int(row.get("real_quantity"), 0) or 0,
                    expiration_date=_parse_d(row.get("expiration_date")),
                    batch=row.get("batch"),
                    in_inventory=_parse_int(row.get("in_inventory"), 0) or 0,
                    is_completed=_parse_bool(row.get("is_completed")),
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        self._bulk_insert(EntryProduct, "entry_products", objects, legacy_ids)

    def _import_bins(self, workspace: Workspace) -> None:
        rows = self._load_rows("bins")
        objects = []
        legacy_ids = []
        for row in rows:
            objects.append(
                Bin(
                    code=row["code"],
                    workspace=workspace,
                    client_id=_map_id(self.id_maps, "clients",
                                      row.get("client_id")),
                    status=row["status"],
                    entry_id=_map_id(self.id_maps, "entries",
                                     row.get("entry_id")),
                    position_id=_map_id(
                        self.id_maps, "positions", row.get("position_id")),
                    zpl_code=row.get("zpl_code"),
                    dispatched_date=_parse_dt(row.get("dispatched_date")),
                    is_processed=_parse_bool(row.get("is_processed")),
                    was_qr_validated=_parse_bool(row.get("was_qr_validated")),
                    is_active=_parse_bool(row.get("is_active", True)),
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        self._bulk_insert(Bin, "bins", objects, legacy_ids)

    def _import_bin_products(self) -> None:
        rows = self._load_rows("bin_products")
        objects = []
        legacy_ids = []
        for row in rows:
            objects.append(
                BinProduct(
                    bin_id=_map_id(self.id_maps, "bins", row.get("bin_id")),
                    entry_product_id=_map_id(
                        self.id_maps, "entry_products", row.get("entry_product_id")),
                    batch=row.get("batch"),
                    expiration_date=_parse_d(row.get("expiration_date")),
                    quantity=_parse_int(row.get("quantity"), 0) or 0,
                    is_active=_parse_bool(row.get("is_active", True)),
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        self._bulk_insert(BinProduct, "bin_products", objects, legacy_ids)

    def _import_exits(self) -> None:
        rows = self._load_rows("exits")
        objects = []
        legacy_ids = []
        for row in rows:
            objects.append(
                Exit(
                    client_id=_map_id(self.id_maps, "clients",
                                      row.get("client_id")),
                    branch_id=_map_id(self.id_maps, "branches",
                                      row.get("branch_id")),
                    status=row["status"],
                    exit_date=_parse_d(row.get("exit_date")),
                    code=row["code"],
                    out_type=row.get("out_type"),
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        self._bulk_insert(Exit, "exits", objects, legacy_ids)

    def _import_exit_product_groups(self) -> None:
        rows = self._load_rows("exit_product_groups")
        objects = []
        legacy_ids = []
        for row in rows:
            objects.append(
                ExitProductGroup(
                    exit_id=_map_id(self.id_maps, "exits", row.get("exit_id")),
                    product_id=_map_id(
                        self.id_maps, "products", row.get("product_id")),
                    quantity=_parse_int(row.get("quantity"), 0) or 0,
                    bin_or_batch=row.get("bin_or_batch"),
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        self._bulk_insert(ExitProductGroup,
                          "exit_product_groups", objects, legacy_ids)

    def _import_exit_products(self) -> None:
        rows = self._load_rows("exit_products")
        objects = []
        legacy_ids = []
        for row in rows:
            bin_product_id = _map_id(
                self.id_maps, "bin_products", row.get("bin_entry_product_id"))
            objects.append(
                ExitProduct(
                    exit_product_group_id=_map_id(
                        self.id_maps, "exit_product_groups", row.get("exit_product_group_id")),
                    bin_product_id=bin_product_id,
                    quantity=_parse_int(row.get("quantity"), 0) or 0,
                    is_picking=_parse_bool(row.get("is_picking")),
                    is_processed=_parse_bool(row.get("is_processed")),
                    was_qr_validated=_parse_bool(row.get("was_qr_validated")),
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        self._bulk_insert(ExitProduct, "exit_products", objects, legacy_ids)

    def _import_bin_movements(self) -> None:
        rows = self._load_rows("bin_movements")
        objects = []
        legacy_ids = []
        skipped = 0
        for row in rows:
            bin_id = _map_id(self.id_maps, "bins", row.get("bin_entry_id"))
            if not bin_id:
                skipped += 1
                continue
            objects.append(
                BinMovement(
                    bin_id=bin_id,
                    is_active=_parse_bool(row.get("is_active", True)),
                    movement_type=row["movement_type"],
                    old_status=row.get("old_status"),
                    new_status=row.get("new_status"),
                    exit_id=_map_id(self.id_maps, "exits", row.get("exit_id")),
                    exit_code=row.get("exit_code"),
                    is_picking=_parse_bool(row.get("is_picking")) if row.get(
                        "is_picking") not in (None, "") else None,
                    quantity_subtracted=_parse_int(
                        row.get("quantity_subtracted")),
                    quantity_remaining=_parse_int(
                        row.get("quantity_remaining")),
                    initial_quantity=_parse_int(row.get("initial_quantity")),
                    position_id=_map_id(
                        self.id_maps, "positions", row.get("position_id")),
                    notes=row.get("notes"),
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        if skipped:
            self.warnings.append(
                f"bin_movements omitidos (bin legacy sin mapeo): {skipped}")
        self._bulk_insert(BinMovement, "bin_movements", objects, legacy_ids)

    def _import_exit_movements(self) -> None:
        rows = self._load_rows("exit_movements")
        objects = []
        legacy_ids = []
        skipped = 0
        for row in rows:
            exit_id = _map_id(self.id_maps, "exits", row.get("exit_id"))
            if not exit_id:
                skipped += 1
                continue
            objects.append(
                ExitMovement(
                    exit_id=exit_id,
                    movement_type=row["movement_type"],
                    old_status=row.get("old_status"),
                    new_status=row.get("new_status"),
                    product_id=_map_id(
                        self.id_maps, "products", row.get("product_id")),
                    product_sku=row.get("product_sku"),
                    product_name=row.get("product_name"),
                    bin_id=_map_id(self.id_maps, "bins",
                                   row.get("bin_entry_id")),
                    bin_code=row.get("bin_code"),
                    old_bin_code=row.get("old_bin_code"),
                    new_bin_code=row.get("new_bin_code"),
                    quantity=_parse_int(row.get("quantity")),
                    old_quantity=_parse_int(row.get("old_quantity")),
                    new_quantity=_parse_int(row.get("new_quantity")),
                    is_picking=_parse_bool(row.get("is_picking")) if row.get(
                        "is_picking") not in (None, "") else None,
                    notes=row.get("notes"),
                    metadata=_parse_json(row.get("metadata")),
                    created_at=_parse_dt(row.get("created_at")),
                    updated_at=_parse_dt(row.get("updated_at")),
                )
            )
            legacy_ids.append(str(row["id"]))
        if skipped:
            self.warnings.append(
                f"exit_movements omitidos (exit legacy sin mapeo): {skipped}")
        self._bulk_insert(ExitMovement, "exit_movements", objects, legacy_ids)

    def _reset_sequences(self) -> None:
        if connection.vendor != "postgresql":
            return
        models_to_reset = [
            Tariff,
            Storage,
            Cava,
            Position,
            Client,
            apps.get_model("core", "Branch"),
            Profile,
            CustomUser,
            Product,
            Entry,
            EntryProduct,
            Bin,
            BinProduct,
            Exit,
            ExitProductGroup,
            ExitProduct,
            BinMovement,
            ExitMovement,
        ]
        with connection.cursor() as cursor:
            for model in models_to_reset:
                table = model._meta.db_table
                pk = model._meta.pk.column
                cursor.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', '{pk}'), "
                    f"COALESCE((SELECT MAX({pk}) FROM {table}), 1), true)"
                )
