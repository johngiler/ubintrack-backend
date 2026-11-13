from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0009_upsert_cdf_bins_camara_9"),
    ]

    operations = [
        migrations.AddField(
            model_name="bin",
            name="label_include_observation",
            field=models.BooleanField(
                default=False,
                help_text="Si está activo, la observación aparece en preview y ZPL (una línea).",
                verbose_name="incluir observación en etiqueta",
            ),
        ),
    ]
