from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0017_cavablock_aisle_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="cava",
            name="plan_layout",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Metadatos solo visuales: puertas, eje de profundidad. No afecta nomenclatura ni generate.",
                verbose_name="plano gráfico",
            ),
        ),
    ]
