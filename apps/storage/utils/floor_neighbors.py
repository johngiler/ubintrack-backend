"""Vecinos de sección para posiciones de piso (nomenclatura localizable)."""

from __future__ import annotations


def is_named_section(block) -> bool:
    kind = getattr(block, "kind", None) or (block.get("kind") if isinstance(block, dict) else None)
    code = getattr(block, "code", None)
    if isinstance(block, dict):
        code = block.get("code")
    return kind == "section" and bool(str(code or "").strip())


def section_neighbors(blocks, index: int) -> list:
    """Secciones inmediatas prev/next con token (pasillo, túnel o división)."""
    list_ = list(blocks or [])
    found = []
    if index > 0:
        prev = list_[index - 1]
        if is_named_section(prev):
            found.append(prev)
    if index < len(list_) - 1:
        nxt = list_[index + 1]
        if is_named_section(nxt):
            found.append(nxt)
    return found


def floor_hall_token(neighbors) -> str:
    tokens = []
    for block in neighbors or []:
        code = getattr(block, "code", None)
        if isinstance(block, dict):
            code = block.get("code")
        token = str(code or "").strip()
        if token:
            tokens.append(token)
    return "-".join(tokens)


def floor_is_active(neighbors) -> bool:
    return len(neighbors or []) >= 1
