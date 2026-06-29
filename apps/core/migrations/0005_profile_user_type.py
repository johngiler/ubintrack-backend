from django.db import migrations, models


def set_profile_user_types(apps, schema_editor):
    Profile = apps.get_model("core", "Profile")
    for profile in Profile.objects.all():
        if profile.name and profile.name.strip().lower() == "cliente":
            profile.user_type = "cliente"
        else:
            profile.user_type = "empleado"
        profile.save(update_fields=["user_type"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_delete_clientuser"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="user_type",
            field=models.CharField(
                blank=True,
                choices=[("empleado", "Empleado"), ("cliente", "Cliente")],
                max_length=15,
                null=True,
                verbose_name="tipo de usuario",
            ),
        ),
        migrations.RunPython(set_profile_user_types, migrations.RunPython.noop),
    ]
