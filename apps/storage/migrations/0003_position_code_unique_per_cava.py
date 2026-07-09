from django.db import migrations, models


def dedupe_position_slots(apps, schema_editor):
    # SQL directo: evita señales post_delete que en PostgreSQL dejan
    # "pending trigger events" y bloquean ALTER TABLE en la misma migración.
    schema_editor.execute(
        """
        DELETE FROM storage_position p
        WHERE p.id IN (
            SELECT p2.id
            FROM storage_position p2
            INNER JOIN (
                SELECT cava_id, hall, rack, level, place, MIN(id) AS keep_id
                FROM storage_position
                GROUP BY cava_id, hall, rack, level, place
                HAVING COUNT(*) > 1
            ) d ON p2.cava_id = d.cava_id
                AND p2.hall = d.hall
                AND p2.rack = d.rack
                AND p2.level = d.level
                AND p2.place = d.place
                AND p2.id <> d.keep_id
        )
        """
    )
    schema_editor.execute(
        """
        UPDATE storage_cava c
        SET total_levels = sub.cnt
        FROM (
            SELECT cava_id, COUNT(*) AS cnt
            FROM storage_position
            GROUP BY cava_id
        ) sub
        WHERE c.id = sub.cava_id
        """
    )


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0002_alter_cava_created_at_alter_cava_is_active_and_more"),
    ]

    operations = [
        migrations.RunPython(
            dedupe_position_slots,
            migrations.RunPython.noop,
            atomic=False,
        ),
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
