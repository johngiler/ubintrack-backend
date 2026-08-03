from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0007_alter_position_hall"),
    ]

    operations = [
        migrations.AddField(
            model_name="position",
            name="is_push",
            field=models.BooleanField(
                default=False,
                help_text="Marca el conducto (cava+pasillo+rack+nivel) como almacenamiento push-back.",
                verbose_name="es push?",
            ),
        ),
    ]
