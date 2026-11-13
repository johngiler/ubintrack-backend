from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workspaces", "0007_workspace_printer_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="bin_label_include_observation",
            field=models.BooleanField(
                default=False,
                help_text="Si está activo, la observación del bin aparece en preview y ZPL (una línea).",
                verbose_name="Incluir observación en etiqueta de bin",
            ),
        ),
    ]
