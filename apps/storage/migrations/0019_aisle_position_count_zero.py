from django.db import migrations


def zero_aisle_rack_counts(apps, schema_editor):
    CavaBlock = apps.get_model("storage", "CavaBlock")
    CavaBlock.objects.filter(kind="aisle").update(rack_count=0)


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0018_cava_plan_layout"),
    ]

    operations = [
        migrations.RunPython(zero_aisle_rack_counts, migrations.RunPython.noop),
    ]
