"""Traduce códigos de posición del Excel CDF a nomenclatura Ubintrack.

Inventario inicial CDF: la «C1» del Excel es Cámara 9 (no Andén code 1).
Confirmado con operación: todo el listado coincide en cámara 9.
"""

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

# Excel cava token → código de cava Ubintrack (coldcave).
EXCEL_CAVA_TO_UBINTRACK = {
    "1": "9",
}

DASHED_RE = re.compile(r"^C(\d+)-(\d{2})-(\d{2})-(\d{2})-(\d{2})$", re.I)
# Preferir cava de 1 dígito (C1…) antes que compacto de 11 que interpreta C10…
COMPACT_RE = re.compile(r"^C(\d)(\d{2})(\d{2})(\d{2})(\d{2})$", re.I)
# C1040101001 → C1 + aisle + rack + level + place(3)
COMPACT11_C1_RE = re.compile(r"^C1(\d{2})(\d{2})(\d{2})(\d{3})$", re.I)
COMPACT11_RE = re.compile(r"^C(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$", re.I)


def _ubintrack_cava(excel_cava: str) -> tuple[str, str | None]:
    """Devuelve (cava_codigo, nota_opcional de remapeo)."""
    raw = str(int(excel_cava))
    mapped = EXCEL_CAVA_TO_UBINTRACK.get(raw)
    if mapped and mapped != raw:
        return mapped, f"Cava Excel C{raw} → Cámara {mapped}"
    return raw, None


def _level_from_raw(lvl_raw: str) -> int:
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


def _floor_code(cava: str, aisle: str, depth: str) -> str | None:
    """
    Piso / entrepasillo en cámara 9:
      aisle 2 → P1-P2
      aisle 3 o 4 → P3-P4
    Fallback: C{n}-P{aisle}-{place} (luego flex en import)
    """
    aisle_n = int(aisle)
    place = f"{int(depth):02d}"
    if aisle_n == 2:
        return f"C{int(cava)}-P1-P2-{place}"
    if aisle_n in (3, 4):
        return f"C{int(cava)}-P3-P4-{place}"
    return f"C{int(cava)}-P{aisle_n}-{place}"


def _finish(
    excel: str,
    code: str | None,
    note: str,
    cava_note: str | None,
    *,
    parts: dict | None = None,
) -> dict:
    if cava_note and code:
        note = f"{note} ({cava_note})"
    elif cava_note and not code:
        note = f"{note} ({cava_note})"
    return {
        "excel": excel,
        "mapped_code": code,
        "mapped": bool(code),
        "note": note,
        "parts": parts,
    }


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

    def _parts(cava: str, aisle: str, rack: str, level: int, depth: str) -> dict:
        return {
            "cava": int(cava),
            "aisle": int(aisle),
            "rack": int(rack),
            "level": level,
            "depth": int(depth),
            "place": DEPTH_MAP.get(depth),
        }

    m = DASHED_RE.fullmatch(excel)
    if m:
        excel_cava, aisle, rack, lvl_raw, depth = m.groups()
        cava, cava_note = _ubintrack_cava(excel_cava)
        level = _level_from_raw(lvl_raw)
        parts = _parts(cava, aisle, rack, level, depth)
        if level == 0:
            code = _floor_code(cava, aisle, depth)
            return _finish(
                excel,
                code,
                f"Posición Excel {excel} → {code} (piso)",
                cava_note,
                parts=parts,
            )
        code = _rack_code(cava, aisle, rack, level, depth)
        if not code:
            return _finish(
                excel,
                None,
                f"Profundidad Excel {depth} no mapeable",
                cava_note,
                parts=parts,
            )
        return _finish(
            excel,
            code,
            f"Posición Excel {excel} → {code}",
            cava_note,
            parts=parts,
        )

    m = COMPACT_RE.fullmatch(excel)
    excel_cava = aisle = rack = lvl_raw = depth = None
    if m:
        excel_cava, aisle, rack, lvl_raw, depth = m.groups()
    else:
        m11 = COMPACT11_C1_RE.fullmatch(excel)
        if m11:
            # Cava Excel fija C1 → Cámara 9; place de 3 dígitos (001→01).
            aisle, rack, lvl_raw, place3 = m11.groups()
            excel_cava = "1"
            depth = place3[-2:]
        else:
            m = COMPACT11_RE.fullmatch(excel)
            if m:
                excel_cava, aisle, rack, lvl_raw, depth = m.groups()

    if excel_cava is not None:
        cava, cava_note = _ubintrack_cava(excel_cava)
        level = _level_from_raw(lvl_raw)
        parts = _parts(cava, aisle, rack, level, depth)
        if level == 0:
            code = _floor_code(cava, aisle, depth)
            return _finish(
                excel,
                code,
                f"Posición Excel {excel} → {code} (compacto/piso)",
                cava_note,
                parts=parts,
            )
        code = _rack_code(cava, aisle, rack, level, depth)
        if not code:
            return _finish(
                excel,
                None,
                f"Posición compacta {excel} no mapeable",
                cava_note,
                parts=parts,
            )
        return _finish(
            excel,
            code,
            f"Posición Excel {excel} → {code} (compacto)",
            cava_note,
            parts=parts,
        )

    return {
        "excel": excel,
        "mapped_code": None,
        "mapped": False,
        "note": f"Formato de posición no reconocido: {excel}",
        "parts": None,
    }
