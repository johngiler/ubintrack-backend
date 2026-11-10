from django.db import migrations, models
from django.db.models import Q


def fix_position_flags(apps, schema_editor):
    """
    Corrige inconsistencias históricas:
    - inactiva → sin push ni picking
    - push y picking a la vez → se conserva push, se apaga picking
    """
    Position = apps.get_model("storage", "Position")

    Position.objects.filter(is_active=False).filter(
        Q(is_push=True) | Q(is_picking=True)
    ).update(is_push=False, is_picking=False)

    Position.objects.filter(is_active=True, is_push=True, is_picking=True).update(
        is_picking=False
    )


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0029_cavablock_stretch_full"),
    ]

    operations = [
        migrations.RunPython(fix_position_flags, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="position",
            constraint=models.CheckConstraint(
                check=(
                    (Q(is_active=True) | Q(is_push=False))
                    & (Q(is_active=True) | Q(is_picking=False))
                    & (Q(is_push=False) | Q(is_picking=False))
                ),
                name="position_flags_active_push_picking",
            ),
        ),
    ]
