"""Carga / re-sincroniza el inventario inicial CDF (JSON) en coldcave.

Modo upsert (por defecto):
- actualiza bins ya creados del mismo import
- crea solo los que falten
- recalcula posiciones con el mapper actual (C1 Excel → Cámara 9)
"""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
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
from .place_flex import FlexAllocator, PlaceContext, is_floor_or_tunnel

DEFAULT_JSON = (
    Path(__file__).resolve().parents[4]
    / "data"
    / "imports"
    / "coldcave"
    / "inventario_inicial_cdf.json"
)

EXCEL_ROW_RE = re.compile(r"^Fila Excel:\s*(\d+)\s*$", re.I | re.M)
EXCEL_POS_RE = re.compile(r"Posición Excel\s+(\S+)", re.I)


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
    flex_note: str | None = None,
    final_code: str | None = None,
) -> str:
    lines = [marker]
    lines.append("Corrección: Excel C1 = Cámara 9 (no Andén)")
    excel_row = row.get("excel_row")
    if excel_row:
        lines.append(f"Fila Excel: {excel_row}")

    excel_pos = row.get("excel_position") or ""
    note = row.get("position_map_note") or ""
    assigned = final_code or mapped_code
    if assigned and position_exists:
        lines.append(note or f"Posición Excel {excel_pos} → {assigned}")
    elif mapped_code and not position_exists:
        lines.append(
            f"Posición Excel {excel_pos} mapeada a {mapped_code} "
            "pero no existe en el owner (sin asignar)"
        )
    elif excel_pos:
        lines.append(
            note or f"Posición Excel {excel_pos} sin equivalente en Ubintrack"
        )

    if flex_note:
        lines.append(flex_note)

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


def _excel_row_from_obs(observation: str) -> int | None:
    match = EXCEL_ROW_RE.search(observation or "")
    if not match:
        return None
    return int(match.group(1))


def _excel_pos_from_obs(observation: str) -> str:
    match = EXCEL_POS_RE.search(observation or "")
    return (match.group(1).strip().upper() if match else "")


def _fingerprint(
    *,
    excel_position: str,
    product_id: int,
    production_date: date | None,
    expiration_date: date | None,
    quantity: int,
    barcode: str,
    sap_code: str,
) -> tuple:
    return (
        (excel_position or "").upper(),
        product_id,
        production_date.isoformat() if production_date else "",
        expiration_date.isoformat() if expiration_date else "",
        int(quantity or 0),
        barcode or "",
        sap_code or "",
    )


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


def _get_or_create_cdf_entries(
    *,
    workspace: Workspace,
    storage: Storage,
    marker: str,
    client_ids: set[int],
    report: dict[str, Any],
) -> dict[int, Entry]:
    """Reutiliza entradas CDF existentes por cliente; crea si faltan."""
    entry_by_client: dict[int, Entry] = {}
    existing_bins = Bin.objects.filter(
        workspace=workspace,
        observation__startswith=marker,
        client_id__in=client_ids,
    ).select_related("entry")

    for bin_obj in existing_bins:
        if bin_obj.client_id and bin_obj.entry_id:
            entry_by_client.setdefault(bin_obj.client_id, bin_obj.entry)

    today = timezone.localdate()
    for client_id in client_ids:
        if client_id in entry_by_client:
            continue
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
        report.setdefault("created_entries", []).append(
            {"id": entry.id, "code": entry.code, "client": client.name}
        )
    return entry_by_client


def _get_or_create_entry_product(
    *,
    entry: Entry,
    product,
    production_date: date | None,
    expiration_date: date | None,
    batch: str | None,
    cache: dict[tuple, EntryProduct],
) -> EntryProduct:
    key = (
        entry.id,
        product.id,
        production_date,
        expiration_date,
        batch or "",
    )
    if key in cache:
        return cache[key]

    qs = EntryProduct.objects.filter(
        entry=entry,
        product=product,
        production_date=production_date,
        expiration_date=expiration_date,
        batch=batch or None,
    )
    ep = qs.first()
    if ep is None:
        # batch blank may be stored as "" or NULL
        ep = EntryProduct.objects.filter(
            entry=entry,
            product=product,
            production_date=production_date,
            expiration_date=expiration_date,
        ).filter(models.Q(batch__isnull=True) | models.Q(batch="")).first()
    if ep is None:
        ep = EntryProduct(
            entry=entry,
            product=product,
            quantity=0,
            real_quantity=0,
            production_date=production_date,
            expiration_date=expiration_date,
            batch=batch,
            in_inventory=0,
            is_completed=False,
            is_active=True,
        )
        ep.save()
    cache[key] = ep
    return ep


@transaction.atomic
def import_initial_bins(
    data: dict[str, Any],
    *,
    dry_run: bool = False,
    upsert: bool = True,
) -> dict[str, Any]:
    workspace = resolve_workspace(data.get("workspace_slug") or "coldcave")
    source_file = data.get("source_file") or "Inventario Inicial CDF.xlsx"
    marker = data.get("import_marker") or f"Importado desde archivo {source_file}"

    report: dict[str, Any] = {
        "workspace": workspace.slug,
        "source_file": source_file,
        "dry_run": dry_run,
        "upsert": upsert,
        "skipped_empty": 0,
        "skipped_product": 0,
        "created_bins": 0,
        "updated_bins": 0,
        "created_entries": [],
        "position_unmapped": 0,
        "position_flex": 0,
        "errors": [],
        "warnings": [],
    }

    if not upsert and already_imported(workspace, marker):
        report["errors"].append(
            f"Importación ya aplicada (marker: {marker}). Use upsert."
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

    # Solo Cámara 9 activa: no ubicar en push/inactivas (el KPI las excluye).
    positions = {
        code: pk
        for code, pk in Position.objects.filter(
            cava__storage__workspace=workspace,
            cava__code="9",
            is_active=True,
        ).values_list("code", "id")
    }

    # Ocupadas hoy, menos las de bins CDF (se reasignan en este upsert).
    occupied_now = set(
        Bin.objects.filter(
            workspace=workspace,
            position_id__isnull=False,
            status__in=[
                choices.BinStatus.ALMACENADO,
                choices.BinStatus.EN_DESPACHO,
            ],
        ).values_list("position_id", flat=True)
    )
    cdf_held = set(
        Bin.objects.filter(
            workspace=workspace,
            observation__startswith=marker,
            position_id__isnull=False,
        ).values_list("position_id", flat=True)
    )
    allocator = FlexAllocator(
        code_to_id=positions,
        occupied=set(occupied_now) - set(cdf_held),
        allow_floor=False,
    )

    from .positions import map_excel_position

    # Pasada 1: validar productos + preferred. Exactos primero para no
    # dejar que el flex robe slots legítimos del Excel.
    pending: list[dict[str, Any]] = []
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
        live = map_excel_position(row.get("excel_position"))
        if live.get("mapped_code"):
            mapped_code = live["mapped_code"]
            row = {
                **row,
                "mapped_position_code": mapped_code,
                "position_map_note": live.get("note") or row.get("position_map_note"),
            }

        # Piso/túnel no se asignan en esta importación (solo racks).
        on_floor = is_floor_or_tunnel(mapped_code)
        needs_flex = on_floor or not bool(
            mapped_code and mapped_code in positions
        )
        pending.append(
            {
                "row": row,
                "product": matched.product,
                "matched_note": matched.note,
                "mapped_code": mapped_code,
                "needs_flex": needs_flex,
            }
        )

    prepared: list[dict[str, Any]] = []

    def _finalize(item: dict[str, Any], placement) -> None:
        row = item["row"]
        mapped_code = item["mapped_code"]
        position_id = placement.position_id
        final_code = placement.code
        flex_note = placement.note

        if placement.kind == "exact":
            pass
        elif position_id:
            report["position_flex"] += 1
            report.setdefault("placement_kinds", {})
            kind = placement.kind or "flex"
            report["placement_kinds"][kind] = (
                report["placement_kinds"].get(kind, 0) + 1
            )
            report["warnings"].append(
                f"Fila {row.get('excel_row')}: {flex_note}"
            )
        else:
            report["position_unmapped"] += 1
            report["warnings"].append(
                f"Fila {row.get('excel_row')}: posición sin match "
                f"({row.get('excel_position')} → {mapped_code})"
                + (f" | {flex_note}" if flex_note else "")
            )

        observation = _build_observation(
            marker=marker,
            row=row,
            position_exists=bool(position_id),
            mapped_code=mapped_code,
            flex_note=flex_note if placement.kind not in ("exact",) else None,
            final_code=final_code,
        )
        matched_note = item.get("matched_note")
        if matched_note and str(matched_note).startswith("Match aproximado"):
            observation = f"{observation}\n{matched_note}"

        prepared.append(
            {
                "row": row,
                "product": item["product"],
                "client_id": item["product"].client_id,
                "position_id": position_id,
                "mapped_code": final_code or mapped_code,
                "observation": observation,
                "quantity": int(row.get("quantity") or 1),
                "production_date": _parse_iso_date(row.get("production_date")),
                "expiration_date": _parse_iso_date(row.get("expiration_date")),
                "batch": row.get("batch") or None,
                "lpn": row.get("lpn") or "",
                "barcode": row.get("barcode") or "",
                "sap_code": row.get("sap_code") or "",
                "excel_row": row.get("excel_row"),
                "excel_position": (row.get("excel_position") or "").upper(),
            }
        )

    def _ctx_for(item: dict[str, Any]) -> PlaceContext:
        row = item["row"]
        return PlaceContext(
            product_id=item["product"].id,
            client_id=item["product"].client_id,
            production_date=_parse_iso_date(row.get("production_date")),
            expiration_date=_parse_iso_date(row.get("expiration_date")),
        )

    # Capa 1 primero (homólogo/exacto) para no perder slots legítimos.
    flex_queue: list[dict[str, Any]] = []
    for item in pending:
        ctx = _ctx_for(item)
        if item["needs_flex"]:
            flex_queue.append(item)
            continue
        placement = allocator.try_homologue(item["mapped_code"], ctx)
        if placement.position_id:
            _finalize(item, placement)
        else:
            flex_queue.append(item)

    # Capas 2–3: flex + mejor ubicación (FIFO/FEFO, producto, cliente, reciente).
    for item in flex_queue:
        placement = allocator.resolve(
            excel_position=item["row"].get("excel_position"),
            preferred_code=item["mapped_code"],
            ctx=_ctx_for(item),
        )
        _finalize(item, placement)

    if not prepared:
        report["errors"].append("No hay filas válidas para importar")
        report["aborted"] = True
        return report

    if dry_run:
        existing = Bin.objects.filter(
            workspace=workspace, observation__startswith=marker
        ).count()
        report["would_sync_rows"] = len(prepared)
        report["existing_cdf_bins"] = existing
        report["would_create_bins"] = max(0, len(prepared) - existing)
        report["would_update_bins"] = min(existing, len(prepared))
        return report

    # Índices de bins CDF existentes para upsert.
    existing_bins = list(
        Bin.objects.filter(
            workspace=workspace, observation__startswith=marker
        ).prefetch_related("bin_products__entry_product")
    )
    by_lpn: dict[str, Bin] = {}
    by_excel_row: dict[int, Bin] = {}
    by_fingerprint: dict[tuple, deque[Bin]] = defaultdict(deque)

    for bin_obj in existing_bins:
        obs = bin_obj.observation or ""
        if bin_obj.lpn:
            by_lpn[bin_obj.lpn.strip()] = bin_obj
        excel_row = _excel_row_from_obs(obs)
        if excel_row is not None:
            by_excel_row[excel_row] = bin_obj

        bp = next(iter(bin_obj.bin_products.all()), None)
        product_id = (
            bp.entry_product.product_id
            if bp and bp.entry_product_id
            else None
        )
        if product_id:
            fp = _fingerprint(
                excel_position=_excel_pos_from_obs(obs),
                product_id=product_id,
                production_date=(
                    bp.entry_product.production_date if bp and bp.entry_product else None
                ),
                expiration_date=bp.expiration_date if bp else None,
                quantity=int(bp.quantity or 0) if bp else 0,
                barcode=bin_obj.barcode or "",
                sap_code="",  # no siempre recuperable
            )
            by_fingerprint[fp].append(bin_obj)

    entry_by_client = _get_or_create_cdf_entries(
        workspace=workspace,
        storage=storage,
        marker=marker,
        client_ids={item["client_id"] for item in prepared},
        report=report,
    )
    ep_cache: dict[tuple, EntryProduct] = {}
    occupied_position_ids: set[int] = set()
    freed_candidates: set[int] = set()
    next_code_n = _next_bin_sequence(workspace.id)
    used_bin_ids: set[int] = set()

    def claim_existing(item: dict[str, Any]) -> Bin | None:
        lpn = (item["lpn"] or "").strip()
        if lpn and lpn in by_lpn:
            bin_obj = by_lpn.pop(lpn)
            # keep consistency in other indexes
            return bin_obj

        excel_row = item.get("excel_row")
        if excel_row is not None and int(excel_row) in by_excel_row:
            return by_excel_row.pop(int(excel_row))

        fp = _fingerprint(
            excel_position=item["excel_position"],
            product_id=item["product"].id,
            production_date=item["production_date"],
            expiration_date=item["expiration_date"],
            quantity=item["quantity"],
            barcode=item["barcode"],
            sap_code=item["sap_code"],
        )
        # Intento exacto; si sap vacío en fingerprint viejo, probar sin sap.
        queue = by_fingerprint.get(fp)
        if queue:
            return queue.popleft()
        fp2 = _fingerprint(
            excel_position=item["excel_position"],
            product_id=item["product"].id,
            production_date=item["production_date"],
            expiration_date=item["expiration_date"],
            quantity=item["quantity"],
            barcode=item["barcode"],
            sap_code="",
        )
        queue = by_fingerprint.get(fp2)
        if queue:
            return queue.popleft()
        return None

    for item in prepared:
        client_id = item["client_id"]
        entry = entry_by_client[client_id]
        entry_product = _get_or_create_entry_product(
            entry=entry,
            product=item["product"],
            production_date=item["production_date"],
            expiration_date=item["expiration_date"],
            batch=item["batch"],
            cache=ep_cache,
        )

        has_position = bool(item["position_id"])
        status = (
            choices.BinStatus.ALMACENADO
            if has_position
            else choices.BinStatus.EN_RECIBO
        )

        existing = claim_existing(item) if upsert else None
        if existing is not None and existing.id in used_bin_ids:
            existing = None

        if existing is not None:
            used_bin_ids.add(existing.id)
            if existing.position_id and existing.position_id != item["position_id"]:
                freed_candidates.add(existing.position_id)

            Bin.objects.filter(pk=existing.pk).update(
                client_id=client_id,
                entry_id=entry.id,
                status=status,
                position_id=item["position_id"],
                lpn=item["lpn"],
                barcode=item["barcode"],
                observation=item["observation"],
                is_active=True,
                updated_at=timezone.now(),
            )

            bp = existing.bin_products.first()
            if bp is None:
                bp = BinProduct(
                    bin_id=existing.id,
                    entry_product=entry_product,
                    quantity=item["quantity"],
                    batch=item["batch"],
                    expiration_date=item["expiration_date"],
                    is_active=True,
                )
                models.Model.save(bp)
            else:
                BinProduct.objects.filter(pk=bp.pk).update(
                    entry_product_id=entry_product.id,
                    quantity=item["quantity"],
                    batch=item["batch"],
                    expiration_date=item["expiration_date"],
                    is_active=True,
                    updated_at=timezone.now(),
                )
            report["updated_bins"] += 1
        else:
            code = f"BIN-{next_code_n:010d}"
            next_code_n += 1
            bin_obj = Bin(
                code=code,
                workspace=workspace,
                client_id=client_id,
                entry=entry,
                status=status,
                position_id=item["position_id"],
                lpn=item["lpn"],
                barcode=item["barcode"],
                observation=item["observation"],
                is_active=True,
                is_processed=False,
                was_qr_validated=False,
            )
            models.Model.save(bin_obj)
            used_bin_ids.add(bin_obj.id)
            bp = BinProduct(
                bin=bin_obj,
                entry_product=entry_product,
                quantity=item["quantity"],
                batch=item["batch"],
                expiration_date=item["expiration_date"],
                is_active=True,
            )
            models.Model.save(bp)
            report["created_bins"] += 1

        if item["position_id"]:
            occupied_position_ids.add(item["position_id"])

    # Liberar posiciones Andén / huérfanas que ya no usan bins activos.
    free_ids = set()
    for pos_id in freed_candidates - occupied_position_ids:
        still = Bin.objects.filter(
            position_id=pos_id,
            status__in=[
                choices.BinStatus.ALMACENADO,
                choices.BinStatus.EN_DESPACHO,
            ],
        ).exists()
        if not still:
            free_ids.add(pos_id)
    # También liberar C1 Andén ocupados por bins CDF que ya no apuntan ahí.
    anden_ids = set(
        Position.objects.filter(
            cava__storage__workspace=workspace,
            cava__code="1",
            status=choices.PositionStatus.OCUPADO,
        ).values_list("id", flat=True)
    )
    for pos_id in anden_ids:
        still = Bin.objects.filter(
            position_id=pos_id,
            status__in=[
                choices.BinStatus.ALMACENADO,
                choices.BinStatus.EN_DESPACHO,
            ],
        ).exists()
        if not still:
            free_ids.add(pos_id)

    if free_ids:
        Position.objects.filter(id__in=free_ids).update(
            status=choices.PositionStatus.LIBRE,
            updated_at=timezone.now(),
        )
        report["positions_freed"] = len(free_ids)

    if occupied_position_ids:
        Position.objects.filter(id__in=occupied_position_ids).update(
            status=choices.PositionStatus.OCUPADO,
            updated_at=timezone.now(),
        )

    # Recalcular líneas de entrada a partir de bins CDF de esas entradas.
    entry_ids = [e.id for e in entry_by_client.values()]
    for entry_id in entry_ids:
        eps = list(EntryProduct.objects.filter(entry_id=entry_id))
        for ep in eps:
            total = (
                BinProduct.objects.filter(
                    entry_product=ep, is_active=True, bin__is_active=True
                ).aggregate(total=models.Sum("quantity"))["total"]
                or 0
            )
            EntryProduct.objects.filter(pk=ep.pk).update(
                quantity=total,
                real_quantity=total,
                in_inventory=total,
                is_completed=total > 0 and total >= total,
                updated_at=timezone.now(),
            )

    Entry.objects.filter(id__in=entry_ids).update(
        status=choices.OrderStatus.COMPLETADA,
        updated_at=timezone.now(),
    )

    return report


def write_json(payload: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out
