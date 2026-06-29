from django.db import migrations, models
import django.db.models.deletion


def migrate_client_users(apps, schema_editor):
    CustomUser = apps.get_model("core", "CustomUser")
    ClientUser = apps.get_model("core", "ClientUser")

    CustomUser.objects.filter(user_type="usuario").update(user_type="cliente")

    for cu in ClientUser.objects.select_related("client").iterator():
        if not cu.user_id or not cu.client_id:
            continue
        CustomUser.objects.filter(pk=cu.user_id).update(
            client_id=cu.client_id,
            workspace_id=cu.client.workspace_id,
            user_type="cliente",
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="client",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="users",
                to="core.client",
                verbose_name="cliente",
            ),
        ),
        migrations.AlterField(
            model_name="customuser",
            name="user_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("administrador", "Administrador"),
                    ("empleado", "Empleado"),
                    ("cliente", "Cliente"),
                ],
                max_length=15,
                null=True,
                verbose_name="tipo de usuario",
            ),
        ),
        migrations.RunPython(migrate_client_users, migrations.RunPython.noop),
    ]
