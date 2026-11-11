"""Bins almacenados sin posición → en_recibo (regla operativa)."""

from django.db import migrations


def bins_sin_posicion_a_en_recibo(apps, schema_editor):
    Bin = apps.get_model("inventory", "Bin")
    Bin.objects.filter(status="almacenado", position__isnull=True).update(
        status="en_recibo"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0006_import_coldcave_initial_bins"),
    ]

    operations = [
        migrations.RunPython(bins_sin_posicion_a_en_recibo, migrations.RunPython.noop),
    ]
