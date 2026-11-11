"""Colocación de posiciones CDF en Cámara 9 — 3 capas.

1. Homólogo con proximidad: código exacto o vecino inmediato (mismo pasillo,
   rack ±1, nivel ±1, misma profundidad si existe).
2. Flexible: B→A en pasillos de una profundidad, piso→racks vecinos (sin
   usar piso/túnel salvo allow_floor), P5→P4/P1, P6→P2/P3, clamp de nivel.
3. Mejor ubicación: si 1–2 fallan, score por comportamiento reciente +
   producto/cliente + FIFO/FEFO (producción/expiración) + N1/N2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from apps.inventory.utils.import_initial_bins.positions import (
    COMPACT11_C1_RE,
    COMPACT11_RE,
    COMPACT_RE,
    DASHED_RE,
    DEPTH_MAP,
    _level_from_raw,
    _ubintrack_cava,
    map_excel_position,
)

RACK_RE = re.compile(r"^C(\d+)-(P\d+)-R(\d+)-N(\d+)([A-Z])$", re.I)
FLOOR_ZONE_RE = re.compile(r"^C(\d+)-(P\d+-P\d+)-(\d{2})$", re.I)
TUNNEL_RE = re.compile(r"^C(\d+)-T\d+-\d+$", re.I)
BAD_P3_FLOOR_RE = re.compile(r"^C(\d+)-P3-(\d{2})$", re.I)
BAD_P56_RE = re.compile(r"^C(\d+)-P([56])-(\d{2})$", re.I)

SINGLE_DEPTH_HALLS = {"P1", "P4"}
DEEP_HALLS = ("P2", "P3")
HALL_RACK_MAX = {"P1": 24, "P4": 24, "P2": 18, "P3": 18}


def is_floor_or_tunnel(code: str | None) -> bool:
    if not code:
        return False
    text = code.strip().upper()
    return bool(FLOOR_ZONE_RE.fullmatch(text) or TUNNEL_RE.fullmatch(text))


@dataclass
class FlexPlacement:
    code: str | None
    position_id: int | None
    note: str | None = None
    kind: str = "none"


@dataclass
class PlaceContext:
    """Contexto de la fila para scoring (capa 3)."""

    product_id: int | None = None
    client_id: int | None = None
    production_date: date | None = None
    expiration_date: date | None = None


@dataclass
class FlexAllocator:
    code_to_id: dict[str, int]
    occupied: set[int] = field(default_factory=set)
    allow_floor: bool = False
    _rack_meta: dict[str, tuple[str, int, int, str]] = field(default_factory=dict)
    _floor_codes: dict[str, list[str]] = field(default_factory=dict)
    _deep_by_hall: dict[str, list[str]] = field(default_factory=dict)
    _single_by_hall: dict[str, list[str]] = field(default_factory=dict)
    _all_rack_codes: list[str] = field(default_factory=list)
    # Historial de esta corrida para “comportamiento reciente”
    _recent: list[dict[str, Any]] = field(default_factory=list)
    _by_product_hall: dict[int, list[str]] = field(default_factory=dict)
    _by_client_hall: dict[int, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        single: dict[str, list[tuple[int, int, str]]] = {"P1": [], "P4": []}
        deep_by_hall: dict[str, list[tuple[int, int, str, str]]] = {
            "P2": [],
            "P3": [],
        }
        floors: dict[str, list[tuple[int, str]]] = {}
        all_racks: list[tuple[str, int, int, str, str]] = []

        for code in self.code_to_id:
            m = RACK_RE.fullmatch(code)
            if m:
                _cava, hall, rack_s, level_s, place = m.groups()
                hall = hall.upper()
                rack, level = int(rack_s), int(level_s)
                place = place.upper()
                self._rack_meta[code] = (hall, rack, level, place)
                all_racks.append((hall, rack, level, place, code))
                if hall in SINGLE_DEPTH_HALLS and place == "A":
                    single[hall].append((rack, level, code))
                if hall in DEEP_HALLS:
                    deep_by_hall[hall].append((rack, level, place, code))
                continue
            m = FLOOR_ZONE_RE.fullmatch(code)
            if m:
                _cava, zone, num = m.groups()
                floors.setdefault(zone.upper(), []).append((int(num), code))

        for hall, rows in single.items():
            rows.sort(key=lambda t: (t[0], 0 if t[1] in (1, 2) else 1, t[1]))
            self._single_by_hall[hall] = [c for _r, _l, c in rows]

        for hall, rows in deep_by_hall.items():
            rows.sort(
                key=lambda t: (t[0], 0 if t[1] in (1, 2) else 1, t[1], t[2])
            )
            self._deep_by_hall[hall] = [c for *_rest, c in rows]

        all_racks.sort(
            key=lambda t: (t[0], t[1], 0 if t[2] in (1, 2) else 1, t[2], t[3])
        )
        self._all_rack_codes = [c for *_r, c in all_racks]

        for zone, rows in floors.items():
            rows.sort(key=lambda t: t[0])
            self._floor_codes[zone] = [c for _n, c in rows]

    def claim(
        self, code: str | None, ctx: PlaceContext | None = None
    ) -> FlexPlacement:
        taken = self._take(code)
        if not taken:
            return FlexPlacement(None, None, kind="none")
        return self._finish(taken, note=None, kind="exact", ctx=ctx)

    def try_homologue(
        self, preferred: str | None, ctx: PlaceContext | None = None
    ) -> FlexPlacement:
        """Capa 1 pública: exacto o vecino inmediato."""
        hit = self._try_tier1(preferred, ctx)
        return hit or FlexPlacement(None, None, kind="none")

    def _take(self, code: str | None) -> str | None:
        if not code:
            return None
        if not self.allow_floor and is_floor_or_tunnel(code):
            return None
        pid = self.code_to_id.get(code)
        if pid is None or pid in self.occupied:
            return None
        self.occupied.add(pid)
        return code

    def _remember(self, code: str, ctx: PlaceContext | None, kind: str) -> None:
        meta = self._rack_meta.get(code)
        entry = {
            "code": code,
            "kind": kind,
            "product_id": ctx.product_id if ctx else None,
            "client_id": ctx.client_id if ctx else None,
            "hall": meta[0] if meta else None,
            "rack": meta[1] if meta else None,
            "level": meta[2] if meta else None,
            "place": meta[3] if meta else None,
            "expiration": ctx.expiration_date if ctx else None,
            "production": ctx.production_date if ctx else None,
        }
        self._recent.append(entry)
        if ctx and ctx.product_id and meta:
            self._by_product_hall.setdefault(ctx.product_id, []).append(code)
        if ctx and ctx.client_id and meta:
            self._by_client_hall.setdefault(ctx.client_id, []).append(code)

    def _finish(
        self,
        code: str | None,
        *,
        note: str | None,
        kind: str,
        ctx: PlaceContext | None,
    ) -> FlexPlacement:
        if not code:
            return FlexPlacement(None, None, note=note, kind="none")
        self._remember(code, ctx, kind)
        return FlexPlacement(code, self.code_to_id[code], note=note, kind=kind)

    # ── Capa 1: homólogo + proximidad ─────────────────────────────────

    def _homologue_candidates(self, preferred: str) -> list[str]:
        """Exacto + vecinos rack±1 / nivel±1 misma profundidad y pasillo."""
        meta = self._rack_meta.get(preferred)
        if not meta:
            return [preferred] if preferred in self.code_to_id else []
        hall, rack, level, place = meta
        cava = preferred.split("-", 1)[0]  # C9
        deltas = (
            (0, 0),
            (0, -1),
            (0, 1),
            (-1, 0),
            (1, 0),
            (-1, -1),
            (-1, 1),
            (1, -1),
            (1, 1),
        )
        out: list[str] = []
        seen: set[str] = set()
        for dr, dl in deltas:
            rr, ll = rack + dr, level + dl
            if rr < 1 or ll < 1:
                continue
            code = f"{cava}-{hall}-R{rr}-N{ll}{place}"
            if code in self.code_to_id and code not in seen:
                seen.add(code)
                out.append(code)

        def rank(c: str) -> tuple:
            _h, r, l, _p = self._rack_meta[c]
            return (
                abs(r - rack) + abs(l - level),
                abs(r - rack),
                abs(l - level),
                r,
                l,
            )

        out.sort(key=rank)
        return out

    def _try_tier1(self, preferred: str | None, ctx: PlaceContext | None) -> FlexPlacement | None:
        if not preferred or is_floor_or_tunnel(preferred):
            return None
        for code in self._homologue_candidates(preferred):
            taken = self._take(code)
            if taken:
                if taken == preferred:
                    return self._finish(taken, note=None, kind="exact", ctx=ctx)
                note = (
                    f"Homólogo próximo: prefería {preferred} → {taken}"
                )
                return self._finish(taken, note=note, kind="homologue", ctx=ctx)
        return None

    # ── Capa 2: flexible ──────────────────────────────────────────────

    def _claim_best(
        self,
        codes: list[str],
        *,
        prefer_code: str | None = None,
        prefer_rack: int | None = None,
        prefer_level: int | None = None,
        prefer_place: str | None = None,
    ) -> str | None:
        if prefer_code:
            taken = self._take(prefer_code)
            if taken:
                return taken
            meta = self._rack_meta.get(prefer_code)
            if meta:
                _h, pr, pl, pp = meta
                if prefer_rack is None:
                    prefer_rack = pr
                if prefer_level is None:
                    prefer_level = pl
                if prefer_place is None:
                    prefer_place = pp

        anchor_rack = prefer_rack
        anchor_level = prefer_level if prefer_level is not None else 1
        anchor_place = (prefer_place or "A").upper()

        scored: list[tuple[tuple, str]] = []
        for code in codes:
            pid = self.code_to_id.get(code)
            if pid is None or pid in self.occupied:
                continue
            if not self.allow_floor and is_floor_or_tunnel(code):
                continue
            meta = self._rack_meta.get(code)
            if not meta:
                continue
            _hall, rack, level, place = meta
            ar = anchor_rack if anchor_rack is not None else rack
            rack_dist = abs(rack - ar)
            if level in (1, 2) and anchor_level in (1, 2):
                level_score = 0 if level == anchor_level else 1
            else:
                level_score = 2 + abs(level - anchor_level)
            place_score = abs(ord(place) - ord(anchor_place[0])) if place else 0
            scored.append(
                ((rack_dist, level_score, place_score, rack, level, place), code)
            )
        if not scored:
            return None
        scored.sort(key=lambda t: t[0])
        return self._take(scored[0][1])

    def _try_tier2(
        self,
        *,
        excel: str,
        preferred: str | None,
        parts: dict | None,
        ctx: PlaceContext | None,
    ) -> FlexPlacement | None:
        aisle = parts.get("aisle") if parts else None
        depth = parts.get("depth") if parts else None
        raw_rack = int((parts or {}).get("rack") or 1)
        level = (parts or {}).get("level")
        depth_letter = (
            DEPTH_MAP.get(f"{int(depth):02d}", "A") if depth is not None else "A"
        )

        # B → A en single-depth
        if preferred:
            m = RACK_RE.fullmatch(preferred)
            if m:
                cava, hall, rack_s, level_s, place = m.groups()
                hall = hall.upper()
                place = place.upper()
                rack_n, level_n = int(rack_s), int(level_s)
                if hall in SINGLE_DEPTH_HALLS and place != "A":
                    alt = f"C{int(cava)}-{hall}-R{rack_n}-N{level_n}A"
                    code = self._claim_best(
                        self._single_by_hall.get(hall, []),
                        prefer_code=alt,
                        prefer_rack=rack_n,
                        prefer_level=level_n,
                        prefer_place="A",
                    )
                    if code:
                        note = (
                            f"Flex: Excel {excel or preferred} pedía {place} en "
                            f"{hall} (solo A) → {code}"
                        )
                        return self._finish(code, note=note, kind="flex_b_to_a", ctx=ctx)

                if hall in SINGLE_DEPTH_HALLS:
                    code = self._claim_best(
                        self._single_by_hall.get(hall, []),
                        prefer_code=f"C{int(cava)}-{hall}-R{rack_n}-N1A",
                        prefer_rack=rack_n,
                        prefer_level=min(level_n, 2) if level_n else 1,
                        prefer_place="A",
                    )
                    if code:
                        note = (
                            f"Flex: Excel {excel or preferred} sin slot "
                            f"{preferred} → {code} (continuo {hall})"
                        )
                        return self._finish(code, note=note, kind="flex_level", ctx=ctx)

                if hall in DEEP_HALLS:
                    code = self._claim_best(
                        self._deep_by_hall.get(hall, []) or self._all_rack_codes,
                        prefer_code=preferred,
                        prefer_rack=rack_n,
                        prefer_level=level_n,
                        prefer_place=place,
                    )
                    if code:
                        note = (
                            f"Flex: Excel {excel or preferred} → {code} "
                            f"(profundo cercano)"
                        )
                        return self._finish(code, note=note, kind="flex_deep", ctx=ctx)

        preferred_is_floor = is_floor_or_tunnel(preferred)
        excel_floor = bool(parts and level == 0 and aisle in (2, 3, 4))
        bad_p3 = bool(preferred and BAD_P3_FLOOR_RE.fullmatch(preferred))
        bad_p56 = bool(preferred and BAD_P56_RE.fullmatch(preferred))

        if preferred_is_floor or bad_p3 or excel_floor:
            target_hall = "P2" if (
                aisle == 2 or (preferred and "P1-P2" in (preferred or ""))
            ) else "P3"
            rack = min(max(raw_rack, 1), HALL_RACK_MAX[target_hall])
            prefer = f"C9-{target_hall}-R{rack}-N1{depth_letter}"
            if prefer not in self.code_to_id:
                prefer = f"C9-{target_hall}-R{rack}-N1A"
            if prefer not in self.code_to_id:
                prefer = None

            if self.allow_floor:
                zone = "P1-P2" if target_hall == "P2" else "P3-P4"
                floor_prefer = (
                    f"C9-{zone}-{int(depth):02d}" if depth is not None else None
                )
                code = self._claim_best(
                    self._floor_codes.get(zone, []),
                    prefer_code=floor_prefer if floor_prefer in self.code_to_id else None,
                )
                if code:
                    note = f"Flex: Excel {excel} → piso {code} (allow_floor)"
                    return self._finish(code, note=note, kind="flex_floor", ctx=ctx)

            hall_order = (
                [target_hall, "P3"]
                if target_hall == "P2"
                else [target_hall, "P2"]
            )
            for hall in hall_order:
                code = self._claim_best(
                    self._deep_by_hall.get(hall, []),
                    prefer_code=prefer if hall == target_hall else None,
                    prefer_rack=rack,
                    prefer_level=1,
                    prefer_place=depth_letter if hall == target_hall else "A",
                )
                if code:
                    note = (
                        f"Flex: Excel {excel} era piso ({preferred or '—'}); "
                        f"→ rack {code} (no se usa piso)"
                    )
                    return self._finish(
                        code, note=note, kind="flex_floor_to_rack", ctx=ctx
                    )
            for hall in ("P4", "P1"):
                code = self._claim_best(
                    self._single_by_hall.get(hall, []),
                    prefer_rack=min(raw_rack, 24),
                    prefer_level=1,
                    prefer_place="A",
                )
                if code:
                    note = (
                        f"Flex: Excel {excel} era piso; → {code} "
                        f"(single {hall}, sin piso)"
                    )
                    return self._finish(
                        code, note=note, kind="flex_floor_to_rack", ctx=ctx
                    )

        if bad_p56 or (parts and level == 0 and aisle in (5, 6)):
            target_aisle = aisle if aisle in (5, 6) else None
            if target_aisle is None and preferred:
                m56 = BAD_P56_RE.fullmatch(preferred)
                if m56:
                    target_aisle = int(m56.group(2))
            if target_aisle == 5:
                rack = min(max(raw_rack, 1), 24)
                code = self._claim_best(
                    self._single_by_hall.get("P4", []),
                    prefer_code=f"C9-P4-R{rack}-N1A",
                    prefer_rack=rack,
                    prefer_level=1,
                    prefer_place="A",
                ) or self._claim_best(
                    self._single_by_hall.get("P1", []),
                    prefer_rack=rack,
                    prefer_level=1,
                    prefer_place="A",
                )
                if code:
                    note = (
                        f"Flex: Excel {excel} (pasillo 5) → {code} "
                        f"(una profundidad, cercanía R{rack})"
                    )
                    return self._finish(code, note=note, kind="flex_p5", ctx=ctx)
            if target_aisle == 6:
                rack = min(max(raw_rack, 1), 18)
                prefer = f"C9-P3-R{rack}-N1{depth_letter}"
                if prefer not in self.code_to_id:
                    prefer = f"C9-P2-R{rack}-N1{depth_letter}"
                code = self._claim_best(
                    self._deep_by_hall.get("P3", []) + self._deep_by_hall.get("P2", []),
                    prefer_code=prefer if prefer in self.code_to_id else None,
                    prefer_rack=rack,
                    prefer_level=1,
                    prefer_place=depth_letter,
                )
                if code:
                    note = (
                        f"Flex: Excel {excel} (pasillo 6) → {code} "
                        f"(profundo, cercanía R{rack})"
                    )
                    return self._finish(code, note=note, kind="flex_p6", ctx=ctx)

        return None

    # ── Capa 3: mejor ubicación (reciente + FIFO/FEFO + producto/cliente) ─

    def _score_best(
        self,
        code: str,
        ctx: PlaceContext | None,
        *,
        prefer_rack: int | None = None,
        prefer_hall: str | None = None,
    ) -> tuple:
        meta = self._rack_meta[code]
        hall, rack, level, place = meta
        recent = self._recent[-12:]

        # 1) Comportamiento reciente: seguir el pasillo/rack de las últimas
        recent_hall_bonus = 0
        recent_rack_dist = 50
        if recent:
            halls = [r["hall"] for r in recent if r.get("hall")]
            if halls:
                from collections import Counter

                top_hall = Counter(halls).most_common(1)[0][0]
                recent_hall_bonus = 0 if hall == top_hall else 3
            racks = [r["rack"] for r in recent if r.get("rack") is not None]
            if racks:
                avg_r = sum(racks) / len(racks)
                recent_rack_dist = abs(rack - avg_r)

        # 2) Mismo producto / cliente (agrupar)
        product_bonus = 5
        client_bonus = 5
        if ctx and ctx.product_id:
            prev = self._by_product_hall.get(ctx.product_id) or []
            for pc in prev[-8:]:
                pm = self._rack_meta.get(pc)
                if not pm:
                    continue
                if pm[0] == hall:
                    product_bonus = min(product_bonus, abs(pm[1] - rack))
        if ctx and ctx.client_id:
            prev = self._by_client_hall.get(ctx.client_id) or []
            for pc in prev[-8:]:
                pm = self._rack_meta.get(pc)
                if not pm:
                    continue
                if pm[0] == hall:
                    client_bonus = min(client_bonus, abs(pm[1] - rack) + 0.5)

        # 3) FEFO: vencimiento pronto → niveles bajos (N1/N2) y accesibles
        fefo_score = 0
        if ctx and ctx.expiration_date:
            today = date.today()
            days = (ctx.expiration_date - today).days
            if days <= 90:
                fefo_score = 0 if level <= 2 else level
            elif days <= 180:
                fefo_score = 0 if level <= 3 else level - 2
            else:
                fefo_score = abs(level - 3)  # medios/altos OK

        # 4) FIFO: producción más antigua → más accesible (N1)
        fifo_score = 0
        if ctx and ctx.production_date:
            age_days = (date.today() - ctx.production_date).days
            if age_days >= 120:
                fifo_score = 0 if level <= 2 else level
            else:
                fifo_score = 0 if level >= 2 else 1

        # 5) Ancla Excel si existe
        anchor = abs(rack - prefer_rack) if prefer_rack is not None else 0
        hall_pen = 0 if (not prefer_hall or hall == prefer_hall) else 4

        # 6) Preferir llenar racks ya tocados recientemente (menos fragmentar)
        fill_bonus = 2
        if recent:
            for r in recent:
                if r.get("hall") == hall and r.get("rack") == rack:
                    fill_bonus = 0
                    break

        level_pref = 0 if level in (1, 2) else 1
        place_pref = 0 if place == "A" else ord(place) - ord("A")

        return (
            hall_pen,
            product_bonus,
            client_bonus,
            recent_hall_bonus,
            recent_rack_dist,
            anchor,
            fefo_score,
            fifo_score,
            fill_bonus,
            level_pref,
            place_pref,
            rack,
            level,
            place,
        )

    def _try_tier3(
        self,
        *,
        excel: str,
        preferred: str | None,
        parts: dict | None,
        ctx: PlaceContext | None,
    ) -> FlexPlacement | None:
        prefer_rack = int((parts or {}).get("rack") or 0) or None
        aisle = (parts or {}).get("aisle")
        prefer_hall = None
        if aisle == 1:
            prefer_hall = "P1"
        elif aisle == 2:
            prefer_hall = "P2"
        elif aisle in (3, 4):
            prefer_hall = "P3" if aisle == 3 else "P4"
        elif aisle == 5:
            prefer_hall = "P4"
        elif aisle == 6:
            prefer_hall = "P3"

        if preferred:
            meta = self._rack_meta.get(preferred)
            if meta:
                prefer_hall = meta[0]
                prefer_rack = meta[1]

        scored: list[tuple[tuple, str]] = []
        for code in self._all_rack_codes:
            pid = self.code_to_id.get(code)
            if pid is None or pid in self.occupied:
                continue
            scored.append(
                (
                    self._score_best(
                        code,
                        ctx,
                        prefer_rack=prefer_rack,
                        prefer_hall=prefer_hall,
                    ),
                    code,
                )
            )
        if not scored:
            return None
        scored.sort(key=lambda t: t[0])
        code = self._take(scored[0][1])
        if not code:
            return None
        note = (
            f"Mejor ubicación: Excel {excel or preferred or '—'} sin match "
            f"homólogo/flex → {code} (reciente+FIFO/FEFO+producto/cliente)"
        )
        return self._finish(code, note=note, kind="best_fit", ctx=ctx)

    # ── API pública ───────────────────────────────────────────────────

    def resolve(
        self,
        *,
        excel_position: str | None,
        preferred_code: str | None = None,
        ctx: PlaceContext | None = None,
    ) -> FlexPlacement:
        excel = (excel_position or "").strip().upper()
        preferred = (preferred_code or "").strip().upper() or None
        if not preferred and excel:
            live = map_excel_position(excel)
            preferred = live.get("mapped_code")

        parts = _excel_parts(excel)

        # Capa 1
        hit = self._try_tier1(preferred, ctx)
        if hit:
            return hit

        # Capa 2
        hit = self._try_tier2(
            excel=excel, preferred=preferred, parts=parts, ctx=ctx
        )
        if hit:
            return hit

        # Capa 3
        hit = self._try_tier3(
            excel=excel, preferred=preferred, parts=parts, ctx=ctx
        )
        if hit:
            return hit

        return FlexPlacement(
            None,
            None,
            note=f"Sin posición libre para Excel {excel or preferred}",
            kind="none",
        )


def _excel_parts(excel: str) -> dict | None:
    if not excel:
        return None
    m = DASHED_RE.fullmatch(excel)
    if m:
        excel_cava, aisle, rack, lvl_raw, depth = m.groups()
    else:
        m = COMPACT_RE.fullmatch(excel)
        if m:
            excel_cava, aisle, rack, lvl_raw, depth = m.groups()
        else:
            m = COMPACT11_C1_RE.fullmatch(excel)
            if m:
                aisle, rack, lvl_raw, place3 = m.groups()
                excel_cava = "1"
                depth = place3[-2:]
            else:
                m = COMPACT11_RE.fullmatch(excel)
                if not m:
                    return None
                excel_cava, aisle, rack, lvl_raw, depth = m.groups()

    cava, _note = _ubintrack_cava(excel_cava)
    return {
        "cava": int(cava),
        "aisle": int(aisle),
        "rack": int(rack),
        "level": _level_from_raw(lvl_raw),
        "depth": int(depth),
    }
