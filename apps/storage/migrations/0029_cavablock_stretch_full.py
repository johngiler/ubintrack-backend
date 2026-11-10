from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0028_cavablock_always_visible"),
    ]

    operations = [
        migrations.AddField(
            model_name="cavablock",
            name="stretch_full",
            field=models.BooleanField(
                default=True,
                help_text="Solo section: ocupar todo el eje de la hilera relacionada.",
                verbose_name="100% ancho",
            ),
        ),
    ]
