from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0015_cavablock_label_side"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="cavablock",
            name="depth_codes",
        ),
    ]
