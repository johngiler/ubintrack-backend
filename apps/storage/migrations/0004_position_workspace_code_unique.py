from django.db import migrations, models
import django.db.models.deletion


def populate_position_workspace(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor == "postgresql":
        schema_editor.execute(
            """
            UPDATE storage_position p
            SET workspace_id = s.workspace_id
            FROM storage_cava c
            INNER JOIN storage_storage s ON s.id = c.storage_id
            WHERE p.cava_id = c.id
              AND p.workspace_id IS NULL
            """
        )
        return

    Position = apps.get_model("storage", "Position")
    Cava = apps.get_model("storage", "Cava")

    batch = []
    for position in Position.objects.filter(workspace__isnull=True).iterator(chunk_size=500):
        if not position.cava_id:
            continue
        cava = Cava.objects.select_related("storage").filter(pk=position.cava_id).first()
        if cava and cava.storage_id:
            position.workspace_id = cava.storage.workspace_id
            batch.append(position)
        if len(batch) >= 500:
            Position.objects.bulk_update(batch, ["workspace_id"])
            batch = []
    if batch:
        Position.objects.bulk_update(batch, ["workspace_id"])


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("workspaces", "0001_initial"),
        ("storage", "0003_position_code_unique_per_cava"),
    ]

    operations = [
        migrations.AddField(
            model_name="position",
            name="workspace",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="positions",
                to="workspaces.workspace",
                verbose_name="workspace",
            ),
        ),
        migrations.RunPython(
            populate_position_workspace,
            migrations.RunPython.noop,
            atomic=False,
        ),
        migrations.RemoveConstraint(
            model_name="position",
            name="unique_position_code_per_cava",
        ),
        migrations.AlterField(
            model_name="position",
            name="workspace",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="positions",
                to="workspaces.workspace",
                verbose_name="workspace",
            ),
        ),
        migrations.AddConstraint(
            model_name="position",
            constraint=models.UniqueConstraint(
                fields=("workspace", "code"),
                name="unique_position_code_per_workspace",
            ),
        ),
    ]
