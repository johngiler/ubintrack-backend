"""Parsea el Excel de inventario inicial CDF a filas JSON listas para importar."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .positions import map_excel_position

SOURCE_FILE_DEFAULT = "Inventario Inicial CDF.xlsx"
IMPORT_MARKER_PREFIX = "Importado desde archivo"

MONTHS_ES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def normalize_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_production(
    value: Any,
    expiration: date | None,
) -> tuple[date | None, str | None]:
    """
    Retorna (production_date, forced_note).
    Si solo viene el mes, toma el año de la expiración (día 1).
    """
    if value is None or value == "":
        return None, None
    if isinstance(value, (datetime, date)):
        d = value.date() if isinstance(value, datetime) else value
        return d, None

    text = str(value).strip()
    if not text:
        return None, None

    parsed = _parse_date(text)
    if parsed:
        return parsed, None

    month = MONTHS_ES.get(text.lower())
    if month and expiration:
        year = expiration.year
        # Si el mes de producción queda después del mes de expiración,
        # el año de producción es el anterior (p. ej. Ago → exp Jul/2026).
        if month > expiration.month:
            year = expiration.year - 1
        forced = date(year, month, 1)
        note = (
            f'Producción "{text}" sin año: año tomado de expiración '
            f"({year}) → {forced.isoformat()}"
        )
        return forced, note

    if month and not expiration:
        return None, (
            f'Producción "{text}" sin año y sin expiración para inferir el año'
        )

    return None, f'Producción no interpretable: "{text}"'


def parse_xlsx_to_rows(
    xlsx_path: str | Path,
    *,
    source_file: str | None = None,
) -> dict[str, Any]:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("openpyxl es requerido para parsear el Excel") from exc

    path = Path(xlsx_path)
    source_name = source_file or path.name
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        rows_iter = ws.iter_rows(values_only=True)
        header = next(rows_iter, None)
        if not header:
            return {
                "workspace_slug": "coldcave",
                "source_file": source_name,
                "rows": [],
            }

        rows: list[dict[str, Any]] = []
        for idx, raw in enumerate(rows_iter, start=2):
            if not raw or all(c is None or str(c).strip() == "" for c in raw):
                continue

            excel_pos = _cell_str(raw[0]) if len(raw) > 0 else ""
            lpn = _cell_str(raw[1]) if len(raw) > 1 else ""
            barcode = _cell_str(raw[2]) if len(raw) > 2 else ""
            sap_code = _cell_str(raw[3]) if len(raw) > 3 else ""
            product_name = normalize_name(raw[4]) if len(raw) > 4 else ""
            qty_raw = raw[5] if len(raw) > 5 else None
            production_raw = raw[6] if len(raw) > 6 else None
            expiration_raw = raw[7] if len(raw) > 7 else None

            if product_name.upper() == "VACIO" or sap_code.upper() == "VACIO":
                rows.append(
                    {
                        "excel_row": idx,
                        "skip": True,
                        "skip_reason": "Fila VACIO (sin inventario)",
                        "excel_position": excel_pos,
                        "product_name": product_name,
                    }
                )
                continue

            expiration = _parse_date(expiration_raw)
            production, production_forced = _parse_production(
                production_raw, expiration
            )

            quantity_forced = False
            quantity = None
            if qty_raw is None or qty_raw == "":
                quantity = 1
                quantity_forced = True
            else:
                try:
                    quantity = int(float(qty_raw))
                except (TypeError, ValueError):
                    quantity = 1
                    quantity_forced = True
                if quantity <= 0:
                    quantity = 1
                    quantity_forced = True

            pos = map_excel_position(excel_pos)
            extra_fields: dict[str, str] = {}
            if sap_code:
                extra_fields["Código SAP"] = sap_code

            rows.append(
                {
                    "excel_row": idx,
                    "skip": False,
                    "skip_reason": None,
                    "excel_position": pos["excel"],
                    "mapped_position_code": pos["mapped_code"],
                    "position_map_note": pos["note"],
                    "lpn": lpn,
                    "barcode": barcode,
                    "sap_code": sap_code,
                    "product_name": product_name,
                    "quantity": quantity,
                    "quantity_forced_default": quantity_forced,
                    "production_date": production.isoformat() if production else None,
                    "production_forced_note": production_forced,
                    "expiration_date": expiration.isoformat() if expiration else None,
                    "batch": None,
                    "extra_fields": extra_fields,
                }
            )
    finally:
        wb.close()

    return {
        "workspace_slug": "coldcave",
        "source_file": source_name,
        "import_marker": f"{IMPORT_MARKER_PREFIX} {source_name}",
        "rows": rows,
    }
