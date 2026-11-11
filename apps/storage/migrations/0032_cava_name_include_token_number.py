# Alinea el nombre visible con el número del token (Anden → Anden 1, Cámara → Cámara 9).

import re

from django.db import migrations

_NUM_RE = re.compile(r"(\d+)$")


def forwards(apps, schema_editor):
    Cava = apps.get_model("storage", "Cava")
    for cava in Cava.objects.all().iterator():
        name = str(cava.name or "").strip()
        code = str(cava.code or "").strip()
        if not name or not code:
            continue
        if re.search(r"\d+\s*$", name):
            continue
        match = _NUM_RE.search(code)
        if not match:
            continue
        Cava.objects.filter(pk=cava.pk).update(name=f"{name} {match.group(1)}")


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0031_cava_token_from_name_clear_prefix"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
