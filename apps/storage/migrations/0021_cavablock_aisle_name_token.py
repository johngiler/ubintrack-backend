# Pasillo: name visible + code = token derivado (P1, TT, …).

from django.db import migrations, models


def aisle_token_from_name(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    initials = []
    number = ""
    for part in raw.split():
        if part.isdigit():
            number = part
            continue
        letter = next((ch for ch in part if ch.isalpha()), "")
        if letter and len(initials) < 2:
            initials.append(letter.upper())
    return "".join(initials) + number


def unique_token(name: str, used: set) -> str:
    import re

    base = aisle_token_from_name(name) or "P"
    if base not in used:
        return base
    stem = re.sub(r"\d+$", "", base) or base
    next_n = 2
    while True:
        candidate = f"{stem}{next_n}"
        if candidate not in used:
            return candidate
        next_n += 1


def forwards(apps, schema_editor):
    CavaBlock = apps.get_model("storage", "CavaBlock")
    Position = apps.get_model("storage", "Position")

    # Por cava: aisles → name + token; racks aisle_code; positions hall.
    cava_ids = (
        CavaBlock.objects.filter(kind="aisle")
        .values_list("cava_id", flat=True)
        .distinct()
    )
    for cava_id in cava_ids:
        aisles = list(
            CavaBlock.objects.filter(cava_id=cava_id, kind="aisle").order_by(
                "sort_order", "id"
            )
        )
        used = set()
        old_to_new = {}
        for aisle in aisles:
            old = str(aisle.code or "").strip()
            if str(aisle.name or "").strip():
                name = str(aisle.name).strip()
            elif old.isdigit():
                name = f"Pasillo {old}"
            elif old:
                name = old
            else:
                name = "Pasillo"
            token = unique_token(name, used)
            used.add(token)
            if old:
                old_to_new[old] = token
            aisle.name = name
            aisle.code = token
            aisle.save(update_fields=["name", "code"])

        if not old_to_new:
            continue

        racks = CavaBlock.objects.filter(cava_id=cava_id, kind="rack")
        for rack in racks:
            linked = str(rack.aisle_code or "").strip()
            if linked in old_to_new:
                rack.aisle_code = old_to_new[linked]
                rack.save(update_fields=["aisle_code"])

        positions = Position.objects.filter(cava_id=cava_id)
        for position in positions:
            hall = str(position.hall or "").strip()
            if not hall:
                continue
            new_hall = old_to_new.get(hall)
            if new_hall is None and hall.isdigit():
                # Hall numérico histórico → P{n} (prefijo P de nomenclatura).
                candidate = f"P{hall}"
                new_hall = old_to_new.get(hall, candidate)
            if new_hall and new_hall != hall:
                position.hall = new_hall
                position.save(update_fields=["hall"])


def backwards(apps, schema_editor):
    # No revertimos tokens a números; solo dejamos name vacío.
    CavaBlock = apps.get_model("storage", "CavaBlock")
    CavaBlock.objects.filter(kind="aisle").update(name="")


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0020_alter_cavablock_aisle_code_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="cavablock",
            name="name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Nombre visible del pasillo (ej. Pasillo 1, Tunel 1). El code se deriva de aquí.",
                max_length=64,
                verbose_name="nombre",
            ),
        ),
        migrations.AlterField(
            model_name="cavablock",
            name="code",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Token en el código de posición. En pasillo: derivado del nombre (P1). En rack: inicio de serie.",
                max_length=32,
                verbose_name="código",
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
