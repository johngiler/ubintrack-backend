from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workspaces", "0004_backfill_legacy_workspace_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="sidebar_logo_gradient",
            field=models.BooleanField(
                default=False,
                help_text="Aplica un fondo con los colores de marca detrás del logo en el menú lateral.",
                verbose_name="Fondo gradiente en logo del sidebar",
            ),
        ),
    ]
