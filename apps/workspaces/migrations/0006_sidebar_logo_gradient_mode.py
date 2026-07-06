from django.db import migrations, models


def migrate_gradient_boolean_to_mode(apps, schema_editor):
    Workspace = apps.get_model("workspaces", "Workspace")
    Workspace.objects.filter(sidebar_logo_gradient=True).update(sidebar_logo_gradient_mode="both")


class Migration(migrations.Migration):

    dependencies = [
        ("workspaces", "0005_workspace_sidebar_logo_gradient"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="sidebar_logo_gradient_mode",
            field=models.CharField(
                choices=[
                    ("none", "Desactivado"),
                    ("light", "Solo modo claro"),
                    ("dark", "Solo modo oscuro"),
                    ("both", "Ambos modos"),
                ],
                default="none",
                help_text="Aplica un fondo con los colores de marca detrás del logo en el menú lateral.",
                max_length=8,
                verbose_name="Fondo gradiente en logo del sidebar",
            ),
        ),
        migrations.RunPython(migrate_gradient_boolean_to_mode, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="workspace",
            name="sidebar_logo_gradient",
        ),
    ]
