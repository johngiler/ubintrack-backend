from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0027_alter_cavablock_depth"),
    ]

    operations = [
        migrations.AddField(
            model_name="cavablock",
            name="always_visible",
            field=models.BooleanField(
                default=False,
                help_text="Solo floor: mostrar posiciones aunque el plano tenga ocultas las de piso.",
                verbose_name="siempre visible",
            ),
        ),
    ]
