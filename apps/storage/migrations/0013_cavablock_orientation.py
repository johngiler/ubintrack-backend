from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0012_remove_depth_on_level"),
    ]

    operations = [
        migrations.AddField(
            model_name="cavablock",
            name="orientation",
            field=models.CharField(
                choices=[("horizontal", "Horizontal"), ("vertical", "Vertical")],
                default="horizontal",
                help_text="Horizontal acuesta los niveles. Vertical los apila.",
                max_length=12,
                verbose_name="orientación",
            ),
        ),
    ]
