"""Traduce códigos de posición del Excel CDF a nomenclatura Ubintrack."""

from __future__ import annotations

import re

DEPTH_MAP = {
    "01": "A",
    "02": "B",
    "03": "C",
    "04": "D",
    "05": "E",
    "06": "F",
    "07": "G",
    "08": "H",
    "09": "I",
    "10": "J",
}

DASHED_RE = re.compile(r"^C(\d+)-(\d{2})-(\d{2})-(\d{2})-(\d{2})$", re.I)
COMPACT_RE = re.compile(r"^C(\d)(\d{2})(\d{2})(\d{2})(\d{2})$", re.I)
COMPACT11_RE = re.compile(r"^C(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$", re.I)


def _level_from_raw(lvl_raw: str) -> int | None:
    value = int(lvl_raw)
    if value == 0:
        return 0
    if value % 10 == 0 and value >= 10:
        return value // 10
    return value


def _rack_code(
    cava: str,
    aisle: str,
    rack: str,
    level: int,
    depth: str,
) -> str | None:
    place = DEPTH_MAP.get(depth)
    if not place:
        return None
    hall = f"P{int(aisle)}"
    return f"C{int(cava)}-{hall}-R{int(rack)}-N{level}{place}"


def _floor_code(cava: str, aisle: str, depth: str) -> str:
    hall = f"P{int(aisle)}"
    return f"C{int(cava)}-{hall}-{int(depth):02d}"


def map_excel_position(raw: str | None) -> dict:
    """
    Devuelve:
      excel, mapped_code, mapped (bool), note
    """
    excel = str(raw or "").strip().upper()
    if not excel:
        return {
            "excel": "",
            "mapped_code": None,
            "mapped": False,
            "note": "Posición Excel vacía",
        }

    m = DASHED_RE.fullmatch(excel)
    if m:
        cava, aisle, rack, lvl_raw, depth = m.groups()
        level = _level_from_raw(lvl_raw)
        if level == 0:
            code = _floor_code(cava, aisle, depth)
            return {
                "excel": excel,
                "mapped_code": code,
                "mapped": True,
                "note": f"Posición Excel {excel} → {code} (piso)",
            }
        code = _rack_code(cava, aisle, rack, level, depth)
        if not code:
            return {
                "excel": excel,
                "mapped_code": None,
                "mapped": False,
                "note": f"Profundidad Excel {depth} no mapeable",
            }
        return {
            "excel": excel,
            "mapped_code": code,
            "mapped": True,
            "note": f"Posición Excel {excel} → {code}",
        }

    m = COMPACT_RE.fullmatch(excel) or COMPACT11_RE.fullmatch(excel)
    if m:
        cava, aisle, rack, lvl_raw, depth = m.groups()
        level = _level_from_raw(lvl_raw)
        if level == 0:
            code = _floor_code(cava, aisle, depth)
            return {
                "excel": excel,
                "mapped_code": code,
                "mapped": True,
                "note": f"Posición Excel {excel} → {code} (compacto/piso)",
            }
        code = _rack_code(cava, aisle, rack, level, depth)
        if not code:
            return {
                "excel": excel,
                "mapped_code": None,
                "mapped": False,
                "note": f"Posición compacta {excel} no mapeable",
            }
        return {
            "excel": excel,
            "mapped_code": code,
            "mapped": True,
            "note": f"Posición Excel {excel} → {code} (compacto)",
        }

    return {
        "excel": excel,
        "mapped_code": None,
        "mapped": False,
        "note": f"Formato de posición no reconocido: {excel}",
    }
