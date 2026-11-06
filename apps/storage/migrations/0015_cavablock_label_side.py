from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0014_cavablock_orientation_help"),
    ]

    operations = [
        migrations.AddField(
            model_name="cavablock",
            name="label_side",
            field=models.CharField(
                choices=[("above", "Arriba"), ("below", "Abajo")],
                default="below",
                help_text="Dónde se escribe el nombre del rack en el plano.",
                max_length=8,
                verbose_name="lado del nombre",
            ),
        ),
    ]
