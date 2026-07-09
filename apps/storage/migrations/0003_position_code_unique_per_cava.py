from django.db import migrations, models


def dedupe_position_slots(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return

    # Mantener la fila con más bins (desempate: id menor). Reasignar FKs antes de borrar.
    schema_editor.execute(
        """
        WITH duplicate_groups AS (
            SELECT cava_id, hall, rack, level, place
            FROM storage_position
            GROUP BY cava_id, hall, rack, level, place
            HAVING COUNT(*) > 1
        ),
        ranked AS (
            SELECT
                p.id,
                FIRST_VALUE(p.id) OVER (
                    PARTITION BY p.cava_id, p.hall, p.rack, p.level, p.place
                    ORDER BY
                        (
                            SELECT COUNT(*)
                            FROM inventory_bin b
                            WHERE b.position_id = p.id
                        ) DESC,
                        p.id ASC
                ) AS keep_id
            FROM storage_position p
            INNER JOIN duplicate_groups d
                ON p.cava_id = d.cava_id
               AND p.hall = d.hall
               AND p.rack = d.rack
               AND p.level = d.level
               AND p.place = d.place
        ),
        losers AS (
            SELECT id, keep_id
            FROM ranked
            WHERE id <> keep_id
        )
        UPDATE inventory_bin b
        SET position_id = l.keep_id
        FROM losers l
        WHERE b.position_id = l.id
        """
    )
    schema_editor.execute(
        """
        WITH duplicate_groups AS (
            SELECT cava_id, hall, rack, level, place
            FROM storage_position
            GROUP BY cava_id, hall, rack, level, place
            HAVING COUNT(*) > 1
        ),
        ranked AS (
            SELECT
                p.id,
                FIRST_VALUE(p.id) OVER (
                    PARTITION BY p.cava_id, p.hall, p.rack, p.level, p.place
                    ORDER BY
                        (
                            SELECT COUNT(*)
                            FROM inventory_bin b
                            WHERE b.position_id = p.id
                        ) DESC,
                        p.id ASC
                ) AS keep_id
            FROM storage_position p
            INNER JOIN duplicate_groups d
                ON p.cava_id = d.cava_id
               AND p.hall = d.hall
               AND p.rack = d.rack
               AND p.level = d.level
               AND p.place = d.place
        ),
        losers AS (
            SELECT id, keep_id
            FROM ranked
            WHERE id <> keep_id
        )
        UPDATE inventory_binmovement bm
        SET position_id = l.keep_id
        FROM losers l
        WHERE bm.position_id = l.id
        """
    )
    schema_editor.execute(
        """
        WITH duplicate_groups AS (
            SELECT cava_id, hall, rack, level, place
            FROM storage_position
            GROUP BY cava_id, hall, rack, level, place
            HAVING COUNT(*) > 1
        ),
        ranked AS (
            SELECT
                p.id,
                FIRST_VALUE(p.id) OVER (
                    PARTITION BY p.cava_id, p.hall, p.rack, p.level, p.place
                    ORDER BY
                        (
                            SELECT COUNT(*)
                            FROM inventory_bin b
                            WHERE b.position_id = p.id
                        ) DESC,
                        p.id ASC
                ) AS keep_id
            FROM storage_position p
            INNER JOIN duplicate_groups d
                ON p.cava_id = d.cava_id
               AND p.hall = d.hall
               AND p.rack = d.rack
               AND p.level = d.level
               AND p.place = d.place
        )
        DELETE FROM storage_position p
        USING ranked r
        WHERE p.id = r.id
          AND p.id <> r.keep_id
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

    atomic = False

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
