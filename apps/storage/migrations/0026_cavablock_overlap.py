from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0025_remove_cavablock_stretch_full"),
    ]

    operations = [
        migrations.AddField(
            model_name="cavablock",
            name="overlap",
            field=models.BooleanField(
                default=False,
                help_text="Solo floor: dibujar las posiciones sobre la sección (pasillo/túnel) vecina.",
                verbose_name="solapar",
            ),
        ),
    ]
