"""One-timer: inventario inicial de bins para owner coldcave.

Lee backend/data/imports/coldcave/inventario_inicial_cdf.json
(generado con: manage.py import_coldcave_initial_bins --parse-only --from-xlsx …).
"""

from pathlib import Path

from django.core.management import call_command
from django.db import migrations


def import_coldcave_bins(apps, schema_editor):
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
        ("inventory", "0005_bin_lpn_barcode_observation_entryproduct_production_date"),
        ("order", "0003_bin_lpn_barcode_observation_entryproduct_production_date"),
    ]

    operations = [
        migrations.RunPython(import_coldcave_bins, migrations.RunPython.noop),
    ]
