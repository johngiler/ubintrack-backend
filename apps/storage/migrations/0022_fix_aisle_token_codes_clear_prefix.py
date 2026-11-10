# Corrige códigos con doble prefijo (P+T1→PT1) y limpia aisle_prefix obsoleto.

from django.db import migrations


def _build_code(*, cava_prefix, cava_code, cava_name, separator, hall, rack, level, place, rack_prefix, level_prefix):
    token = str(cava_code or "").strip()
    if token:
        cava_segment = f"{cava_prefix}{token}"
    else:
        # Fallback mínimo si no hay code de cava.
        cava_segment = str(cava_name or "").strip() or "C"

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
    Position = apps.get_model("storage", "Position")
    Cava = apps.get_model("storage", "Cava")
    PositionNomenclature = apps.get_model("storage", "PositionNomenclature")

    # Ruido del esquema anterior: el prefijo P ya no se antepone al token del pasillo.
    PositionNomenclature.objects.all().update(aisle_prefix="")

    cavas = {c.id: c for c in Cava.objects.all()}
    noms = {
        n.workspace_id: n
        for n in PositionNomenclature.objects.all()
    }

    for position in Position.objects.all().iterator():
        cava = cavas.get(position.cava_id)
        if cava is None:
            continue
        nom = noms.get(position.workspace_id)
        separator = (nom.separator if nom else None) or "-"
        cava_prefix = (nom.cava_prefix if nom else None) or ""
        rack_prefix = (nom.rack_prefix if nom else None) or "R"
        level_prefix = (nom.level_prefix if nom else None) or "N"
        expected = _build_code(
            cava_prefix=cava_prefix,
            cava_code=getattr(cava, "code", "") or "",
            cava_name=getattr(cava, "name", "") or "",
            separator=separator,
            hall=position.hall,
            rack=position.rack,
            level=position.level,
            place=position.place,
            rack_prefix=rack_prefix,
            level_prefix=level_prefix,
        )
        if position.code != expected:
            position.code = expected
            position.save(update_fields=["code"])


def backwards(apps, schema_editor):
    PositionNomenclature = apps.get_model("storage", "PositionNomenclature")
    PositionNomenclature.objects.filter(aisle_prefix="").update(aisle_prefix="P")


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0021_cavablock_aisle_name_token"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
