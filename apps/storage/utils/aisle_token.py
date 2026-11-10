"""Token de pasillo derivado del nombre visible.

Regla: hasta 2 iniciales (primera letra de cada palabra) + último número suelto.
Ej.: «Pasillo 1» → P1 · «Tunel Tunel» → TT · «pedro pelon peludo» → PP.
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_NUM_RE = re.compile(r"\d+")


def aisle_token_from_name(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""

    initials: list[str] = []
    number = ""
    for part in raw.split():
        digits = _NUM_RE.fullmatch(part)
        if digits:
            number = digits.group(0)
            continue
        match = _WORD_RE.search(part)
        if match and len(initials) < 2:
            initials.append(match.group(0)[0].upper())

    return "".join(initials) + number


def unique_aisle_token(name: str, used: set[str] | None = None) -> str:
    """Token único dentro de un conjunto ya usado (PP, PP2, …)."""
    base = aisle_token_from_name(name) or "P"
    taken = used or set()
    if base not in taken:
        return base
    # Si el base ya termina en dígitos, no duplicar; usar sufijo aparte.
    stem = re.sub(r"\d+$", "", base) or base
    next_n = 2
    while True:
        candidate = f"{stem}{next_n}"
        if candidate not in taken:
            return candidate
        next_n += 1
