from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0011_cava_generation_job"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="positionnomenclature",
            name="depth_on_level",
        ),
    ]
