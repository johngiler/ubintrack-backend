from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0024_section_and_floor_blocks"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="cavablock",
            name="stretch_full",
        ),
    ]
