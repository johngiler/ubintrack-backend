import django.db.models.deletion
from django.db import migrations, models


def populate_bin_workspace(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor == "postgresql":
        schema_editor.execute(
            """
            UPDATE inventory_bin b
            SET workspace_id = c.workspace_id
            FROM core_client c
            WHERE b.client_id = c.id
              AND b.workspace_id IS NULL
            """
        )
        schema_editor.execute(
            """
            UPDATE inventory_bin b
            SET workspace_id = c.workspace_id
            FROM order_entry e
            INNER JOIN core_client c ON c.id = e.client_id
            WHERE b.entry_id = e.id
              AND b.workspace_id IS NULL
            """
        )
        return

    Bin = apps.get_model("inventory", "Bin")
    Client = apps.get_model("core", "Client")
    Entry = apps.get_model("order", "Entry")

    batch = []
    for bin_obj in Bin.objects.filter(workspace__isnull=True).iterator(chunk_size=500):
        ws_id = None
        if bin_obj.client_id:
            ws_id = (
                Client.objects.filter(pk=bin_obj.client_id)
                .values_list("workspace_id", flat=True)
                .first()
            )
        if not ws_id and bin_obj.entry_id:
            ws_id = (
                Entry.objects.filter(pk=bin_obj.entry_id)
                .values_list("client__workspace_id", flat=True)
                .first()
            )
        if ws_id:
            bin_obj.workspace_id = ws_id
            batch.append(bin_obj)
        if len(batch) >= 500:
            Bin.objects.bulk_update(batch, ["workspace_id"])
            batch = []
    if batch:
        Bin.objects.bulk_update(batch, ["workspace_id"])


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("core", "0002_initial"),
        ("workspaces", "0001_initial"),
        ("inventory", "0003_alter_bin_created_at_alter_bin_is_active_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="bin",
            name="workspace",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="bins",
                to="workspaces.workspace",
                verbose_name="workspace",
            ),
        ),
        migrations.RunPython(
            populate_bin_workspace,
            migrations.RunPython.noop,
            atomic=False,
        ),
        migrations.AlterField(
            model_name="bin",
            name="code",
            field=models.CharField(
                editable=False, max_length=50, verbose_name="código de bin"
            ),
        ),
        migrations.AlterField(
            model_name="bin",
            name="workspace",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="bins",
                to="workspaces.workspace",
                verbose_name="workspace",
            ),
        ),
        migrations.AddConstraint(
            model_name="bin",
            constraint=models.UniqueConstraint(
                fields=("workspace", "code"),
                name="unique_bin_code_per_workspace",
            ),
        ),
    ]
