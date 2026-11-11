"""One-timer: Excel C1 del CDF = Cámara 9 (no Andén). Reasigna bins importados."""

from django.db import migrations


def reassign_cdf_to_camara_9(apps, schema_editor):
    from apps.inventory.utils.import_initial_bins.reassign_positions import (
        reassign_cdf_bins_to_camara_9,
    )

    reassign_cdf_bins_to_camara_9(workspace_slug="coldcave")


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0007_bins_sin_posicion_en_recibo"),
    ]

    operations = [
        migrations.RunPython(reassign_cdf_to_camara_9, migrations.RunPython.noop),
    ]
