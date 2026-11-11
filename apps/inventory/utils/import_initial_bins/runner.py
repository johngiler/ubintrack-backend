"""Carga el inventario inicial CDF (JSON) en Entry + Bins del owner coldcave."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from django.db import models, transaction
from django.utils import timezone

from apps.core import choices
from apps.core.models import Client
from apps.inventory.models import Bin, BinProduct
from apps.order.models import Entry, EntryProduct
from apps.storage.models import Position, Storage
from apps.workspaces.models import Workspace

from .match import build_product_index, match_product, resolve_workspace

DEFAULT_JSON = (
    Path(__file__).resolve().parents[4] / "data" / "imports" / "coldcave" / "inventario_inicial_cdf.json"
)


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _build_observation(
    *,
    marker: str,
    row: dict[str, Any],
    position_exists: bool,
    mapped_code: str | None,
) -> str:
    lines = [marker]
    excel_pos = row.get("excel_position") or ""
    note = row.get("position_map_note") or ""
    if mapped_code and position_exists:
        lines.append(note or f"Posición Excel {excel_pos} → {mapped_code}")
    elif mapped_code and not position_exists:
        lines.append(
            f"Posición Excel {excel_pos} mapeada a {mapped_code} "
            "pero no existe en el owner (sin asignar)"
        )
    elif excel_pos:
        lines.append(
            note or f"Posición Excel {excel_pos} sin equivalente en Ubintrack"
        )

    if row.get("quantity_forced_default"):
        lines.append("Cantidad ausente/ inválida en Excel: se asignó 1")

    if row.get("production_forced_note"):
        lines.append(str(row["production_forced_note"]))

    for key, value in (row.get("extra_fields") or {}).items():
        if value:
            lines.append(f"{key}: {value}")

    return "\n".join(lines)


def load_rows_from_json(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "rows" not in data:
        raise ValueError("JSON inválido: se espera objeto con clave 'rows'")
    return data


def already_imported(workspace: Workspace, marker: str) -> bool:
    return Bin.objects.filter(
        workspace=workspace,
        observation__startswith=marker,
    ).exists()


@transaction.atomic
def import_initial_bins(
    data: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    workspace = resolve_workspace(data.get("workspace_slug") or "coldcave")
    source_file = data.get("source_file") or "Inventario Inicial CDF.xlsx"
    marker = data.get("import_marker") or f"Importado desde archivo {source_file}"

    report: dict[str, Any] = {
        "workspace": workspace.slug,
        "source_file": source_file,
        "dry_run": dry_run,
        "skipped_empty": 0,
        "skipped_product": 0,
        "created_bins": 0,
        "created_entries": [],
        "position_unmapped": 0,
        "errors": [],
        "warnings": [],
    }

    if already_imported(workspace, marker):
        report["errors"].append(
            f"Importación ya aplicada (marker: {marker}). No se repite."
        )
        report["aborted"] = True
        return report

    index = build_product_index(workspace)
    storage = (
        Storage.objects.filter(workspace=workspace, is_active=True)
        .order_by("id")
        .first()
    )
    if storage is None:
        report["errors"].append("No hay bodega activa para coldcave")
        report["aborted"] = True
        return report

    positions = {
        code: pk
        for code, pk in Position.objects.filter(
            cava__storage__workspace=workspace
        ).values_list("code", "id")
    }

    prepared: list[dict[str, Any]] = []
    for row in data.get("rows") or []:
        if row.get("skip"):
            report["skipped_empty"] += 1
            continue

        matched = match_product(
            index,
            product_name=row.get("product_name") or "",
            sap_code=row.get("sap_code") or "",
            barcode=row.get("barcode") or "",
        )
        if matched.product is None:
            report["skipped_product"] += 1
            report["errors"].append(
                f"Fila {row.get('excel_row')}: {matched.note}"
            )
            continue

        mapped_code = row.get("mapped_position_code")
        position_id = positions.get(mapped_code) if mapped_code else None
        if not position_id:
            report["position_unmapped"] += 1
            report["warnings"].append(
                f"Fila {row.get('excel_row')}: posición sin match "
                f"({row.get('excel_position')} → {mapped_code})"
            )

        observation = _build_observation(
            marker=marker,
            row=row,
            position_exists=bool(position_id),
            mapped_code=mapped_code,
        )
        if matched.note and matched.note.startswith("Match aproximado"):
            observation = f"{observation}\n{matched.note}"

        prepared.append(
            {
                "row": row,
                "product": matched.product,
                "client_id": matched.product.client_id,
                "position_id": position_id,
                "observation": observation,
                "quantity": int(row.get("quantity") or 1),
                "production_date": _parse_iso_date(row.get("production_date")),
                "expiration_date": _parse_iso_date(row.get("expiration_date")),
                "batch": row.get("batch") or None,
                "lpn": row.get("lpn") or "",
                "barcode": row.get("barcode") or "",
            }
        )

    if not prepared:
        report["errors"].append("No hay filas válidas para importar")
        report["aborted"] = True
        return report

    # Agrupar líneas de entrada por cliente + producto + fechas + lote
    entry_lines: dict[int, dict[tuple, dict[str, Any]]] = defaultdict(dict)
    for item in prepared:
        key = (
            item["product"].id,
            item["production_date"],
            item["expiration_date"],
            item["batch"] or "",
        )
        bucket = entry_lines[item["client_id"]]
        if key not in bucket:
            bucket[key] = {
                "product": item["product"],
                "production_date": item["production_date"],
                "expiration_date": item["expiration_date"],
                "batch": item["batch"],
                "quantity": 0,
            }
        bucket[key]["quantity"] += item["quantity"]

    if dry_run:
        report["would_create_bins"] = len(prepared)
        report["would_create_entry_clients"] = sorted(
            Client.objects.filter(id__in=entry_lines.keys()).values_list(
                "name", flat=True
            )
        )
        report["entry_lines"] = {
            str(cid): len(lines) for cid, lines in entry_lines.items()
        }
        return report

    # Crear una Entry completada por cliente
    entry_by_client: dict[int, Entry] = {}
    ep_by_key: dict[tuple, EntryProduct] = {}
    today = timezone.localdate()

    for client_id, lines in entry_lines.items():
        client = Client.objects.get(pk=client_id)
        entry = Entry(
            client=client,
            storage=storage,
            status=choices.OrderStatus.COMPLETADA,
            condition=choices.Conditions.FROZEN,
            in_type=choices.InTypes.PALLETIZADO,
            entry_date=today,
            is_active=True,
        )
        entry.save()
        entry_by_client[client_id] = entry
        report["created_entries"].append(
            {"id": entry.id, "code": entry.code, "client": client.name}
        )

        for key, line in lines.items():
            ep = EntryProduct(
                entry=entry,
                product=line["product"],
                quantity=line["quantity"],
                real_quantity=line["quantity"],
                production_date=line["production_date"],
                expiration_date=line["expiration_date"],
                batch=line["batch"],
                in_inventory=0,
                is_completed=False,
                is_active=True,
            )
            ep.save()
            ep_by_key[(client_id, *key)] = ep

    # Crear bins (sin regenerar tags en caliente)
    occupied_position_ids: set[int] = set()
    next_code_n = _next_bin_sequence(workspace.id)

    for item in prepared:
        client_id = item["client_id"]
        entry = entry_by_client[client_id]
        key = (
            client_id,
            item["product"].id,
            item["production_date"],
            item["expiration_date"],
            item["batch"] or "",
        )
        entry_product = ep_by_key[key]

        code = f"BIN-{next_code_n:010d}"
        next_code_n += 1

        bin_obj = Bin(
            code=code,
            workspace=workspace,
            client_id=client_id,
            entry=entry,
            status=choices.BinStatus.ALMACENADO,
            position_id=item["position_id"],
            lpn=item["lpn"],
            barcode=item["barcode"],
            observation=item["observation"],
            is_active=True,
            is_processed=False,
            was_qr_validated=False,
        )
        # Evita side-effects pesados del save custom en bulk.
        models.Model.save(bin_obj)

        bp = BinProduct(
            bin=bin_obj,
            entry_product=entry_product,
            quantity=item["quantity"],
            batch=item["batch"],
            expiration_date=item["expiration_date"],
            is_active=True,
        )
        models.Model.save(bp)

        if item["position_id"]:
            occupied_position_ids.add(item["position_id"])

        report["created_bins"] += 1

    if occupied_position_ids:
        Position.objects.filter(id__in=occupied_position_ids).update(
            status=choices.PositionStatus.OCUPADO,
            updated_at=timezone.now(),
        )

    # Actualizar inventario de EntryProduct y cerrar entradas iniciales.
    for ep in ep_by_key.values():
        total = (
            BinProduct.objects.filter(entry_product=ep, is_active=True).aggregate(
                total=models.Sum("quantity")
            )["total"]
            or 0
        )
        EntryProduct.objects.filter(pk=ep.pk).update(
            in_inventory=total,
            is_completed=total >= ep.real_quantity,
            updated_at=timezone.now(),
        )

    Entry.objects.filter(id__in=[e.id for e in entry_by_client.values()]).update(
        status=choices.OrderStatus.COMPLETADA,
        updated_at=timezone.now(),
    )

    return report


def _next_bin_sequence(workspace_id: int) -> int:
    last = (
        Bin.objects.filter(workspace_id=workspace_id)
        .order_by("-id")
        .values_list("code", flat=True)
        .first()
    )
    if not last:
        return 1
    try:
        return int(str(last).split("-")[-1]) + 1
    except (TypeError, ValueError):
        return (Bin.objects.filter(workspace_id=workspace_id).count() or 0) + 1


def write_json(payload: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out
