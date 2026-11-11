"""One-timer: recalcular posiciones CDF (Excel C1 → Cámara 9) en upsert."""

from pathlib import Path

from django.core.management import call_command
from django.db import migrations


def upsert_cdf_bins(apps, schema_editor):
    json_path = (
        Path(__file__).resolve().parents[3]
        / "data"
        / "imports"
        / "coldcave"
        / "inventario_inicial_cdf.json"
    )
    if not json_path.exists():
        return
    call_command(
        "import_coldcave_initial_bins",
        data=str(json_path),
        verbosity=1,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0008_reassign_cdf_bins_camara_9"),
    ]

    operations = [
        migrations.RunPython(upsert_cdf_bins, migrations.RunPython.noop),
    ]
