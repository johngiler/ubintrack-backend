# Token de cava por iniciales del nombre (Anden+1 → A1) y cava_prefix vacío.

import re

from django.db import migrations, models

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


def unique_token(base: str, used: set[str]) -> str:
    stem_base = base or "C"
    if stem_base not in used:
        return stem_base
    stem = re.sub(r"\d+$", "", stem_base) or stem_base
    next_n = 2
    while True:
        candidate = f"{stem}{next_n}"
        if candidate not in used:
            return candidate
        next_n += 1


def cava_token_from_legacy(name: str, code: str) -> str:
    """«Anden»+«1» → A1 · «Cámara»+«6» → C6 · «Paola Cabrona» → PC."""
    raw = str(code or "").strip()
    if raw and not raw.isdigit():
        return raw
    name_token = aisle_token_from_name(name)
    if raw.isdigit():
        if name_token and not re.search(r"\d+$", name_token):
            return f"{name_token}{raw}"
        return name_token or raw
    return name_token or "C"


def _build_code(*, cava_code, cava_name, separator, hall, rack, level, place, rack_prefix, level_prefix):
    cava_segment = str(cava_code or "").strip() or aisle_token_from_name(cava_name) or "XX"

    def segment(prefix, value):
        raw = str(value or "").strip()
        if not raw:
            return ""
        return f"{prefix}{raw}"

    aisle_segment = str(hall or "").strip()
    rack_raw = str(rack or "").strip()
    if not rack_raw:
        place_segment = str(place or "").strip()
        parts = [cava_segment, aisle_segment, place_segment]
        return separator.join(part for part in parts if part)

    rack_segment = segment(rack_prefix, rack_raw)
    level_segment = segment(level_prefix, level)
    depth = str(place or "").strip()
    parts = [cava_segment, aisle_segment, rack_segment, f"{level_segment}{depth}"]
    return separator.join(part for part in parts if part)


def forwards(apps, schema_editor):
    Cava = apps.get_model("storage", "Cava")
    Position = apps.get_model("storage", "Position")
    PositionNomenclature = apps.get_model("storage", "PositionNomenclature")

    PositionNomenclature.objects.all().update(cava_prefix="")

    used_by_ws: dict[int, set[str]] = {}
    cava_rows = list(
        Cava.objects.values("id", "name", "code", "storage__workspace_id").order_by("id")
    )
    cava_by_id = {}
    for row in cava_rows:
        ws_id = row["storage__workspace_id"] or 0
        used = used_by_ws.setdefault(ws_id, set())
        raw = str(row["code"] or "").strip()
        name = str(row["name"] or "").strip()
        new_name = name
        # Nombre sin número + código legacy numérico → «Anden»+«1» = «Anden 1».
        if raw.isdigit() and name and not re.search(r"\d+\s*$", name):
            new_name = f"{name} {raw}"
        if raw and not raw.isdigit():
            new_code = unique_token(raw, used)
        else:
            new_code = unique_token(cava_token_from_legacy(new_name or name, raw), used)
        used.add(new_code)
        updates = {}
        if new_code != raw:
            updates["code"] = new_code
        if new_name != name:
            updates["name"] = new_name
        if updates:
            Cava.objects.filter(pk=row["id"]).update(**updates)
        cava_by_id[row["id"]] = {**row, "code": new_code, "name": new_name}

    noms = {n.workspace_id: n for n in PositionNomenclature.objects.all()}

    # Dos fases: evita UniqueViolation al cruzar códigos entre cavas.
    pending: list[tuple[object, str]] = []
    for position in Position.objects.all().iterator():
        cava = cava_by_id.get(position.cava_id)
        if cava is None:
            continue
        nom = noms.get(position.workspace_id)
        separator = (nom.separator if nom else None) or "-"
        rack_prefix = (nom.rack_prefix if nom else None) or "R"
        level_prefix = (nom.level_prefix if nom else None) or "N"
        expected = _build_code(
            cava_code=cava["code"],
            cava_name=cava["name"] or "",
            separator=separator,
            hall=position.hall,
            rack=position.rack,
            level=position.level,
            place=position.place,
            rack_prefix=rack_prefix,
            level_prefix=level_prefix,
        )
        if position.code != expected:
            pending.append((position, expected))

    for position, _expected in pending:
        position.code = f"__MIG{position.pk}__"
        position.save(update_fields=["code"])

    for position, expected in pending:
        position.code = expected
        position.save(update_fields=["code"])


def backwards(apps, schema_editor):
    PositionNomenclature = apps.get_model("storage", "PositionNomenclature")
    PositionNomenclature.objects.filter(cava_prefix="").update(cava_prefix="C")


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0030_fix_position_flags_and_constraint"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="positionnomenclature",
            name="cava_prefix",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Obsoleto: el token de la cava ya viene completo desde el nombre (A1, PC).",
                max_length=8,
                verbose_name="prefijo de cava",
            ),
        ),
        migrations.AlterField(
            model_name="cava",
            name="code",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Token completo de la cava (iniciales del nombre). Ejemplo: Anden 1 → A1, Paola Cabrona → PC.",
                max_length=32,
                verbose_name="código",
            ),
        ),
    ]
