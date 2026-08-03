from django.db import migrations
from django.db.models import Count


PUSH_PLACES = frozenset({"A", "B", "C", "D", "E"})


def mark_depth5_conduits_as_push(apps, schema_editor):
    """
    Conductos (cava+pasillo+rack+nivel) con las 5 profundidades A–E → is_push=True.
    """
    Position = apps.get_model("storage", "Position")

    conduit_groups = (
        Position.objects.values("cava_id", "hall", "rack", "level")
        .annotate(n=Count("id"))
        .filter(n__gte=5)
    )

    for group in conduit_groups.iterator(chunk_size=200):
        places = set(
            Position.objects.filter(
                cava_id=group["cava_id"],
                hall=group["hall"],
                rack=group["rack"],
                level=group["level"],
            ).values_list("place", flat=True)
        )
        if PUSH_PLACES.issubset(places):
            Position.objects.filter(
                cava_id=group["cava_id"],
                hall=group["hall"],
                rack=group["rack"],
                level=group["level"],
            ).update(is_push=True)


def unmark_push(apps, schema_editor):
    Position = apps.get_model("storage", "Position")
    Position.objects.filter(is_push=True).update(is_push=False)


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0008_position_is_push"),
    ]

    operations = [
        migrations.RunPython(mark_depth5_conduits_as_push, unmark_push),
    ]
