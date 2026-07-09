from django.db import migrations, models
from django.db.models import Count, Min


def dedupe_position_slots(apps, schema_editor):
    Position = apps.get_model("storage", "Position")

    duplicate_groups = (
        Position.objects.values("cava_id", "hall", "rack", "level", "place")
        .annotate(n=Count("id"), keep_id=Min("id"))
        .filter(n__gt=1)
    )

    for group in duplicate_groups:
        (
            Position.objects.filter(
                cava_id=group["cava_id"],
                hall=group["hall"],
                rack=group["rack"],
                level=group["level"],
                place=group["place"],
            )
            .exclude(id=group["keep_id"])
            .delete()
        )


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0002_alter_cava_created_at_alter_cava_is_active_and_more"),
    ]

    operations = [
        migrations.RunPython(dedupe_position_slots, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="position",
            name="code",
            field=models.CharField(editable=False, max_length=100, verbose_name="código"),
        ),
        migrations.AddConstraint(
            model_name="position",
            constraint=models.UniqueConstraint(
                fields=("cava", "hall", "rack", "level", "place"),
                name="unique_position_slot_per_cava",
            ),
        ),
        migrations.AddConstraint(
            model_name="position",
            constraint=models.UniqueConstraint(
                fields=("cava", "code"),
                name="unique_position_code_per_cava",
            ),
        ),
    ]
