from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0016_remove_cavablock_depth_codes"),
    ]

    operations = [
        migrations.AddField(
            model_name="cavablock",
            name="aisle_code",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Token del pasillo de este rack. Vacío usa el pasillo más cercano.",
                max_length=32,
                verbose_name="pasillo",
            ),
        ),
    ]
