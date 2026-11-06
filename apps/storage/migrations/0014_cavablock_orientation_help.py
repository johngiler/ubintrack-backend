from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0013_cavablock_orientation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="cavablock",
            name="orientation",
            field=models.CharField(
                choices=[("horizontal", "Horizontal"), ("vertical", "Vertical")],
                default="horizontal",
                help_text="Horizontal pone este grupo en una fila. Vertical apila los racks.",
                max_length=12,
                verbose_name="orientación",
            ),
        ),
    ]
