# Generated manually for Artifact.POSITION, Event.STATUS, position targets

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="event",
            field=models.CharField(
                choices=[
                    ("created", "Creado"),
                    ("updated", "Actualizado"),
                    ("deleted", "Eliminado"),
                    ("status", "Estado"),
                ],
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="notification",
            name="artifact",
            field=models.CharField(
                blank=True,
                choices=[
                    ("entry", "Entrada"),
                    ("exit", "Salida"),
                    ("bin", "Bin"),
                    ("position", "Posición"),
                ],
                default="",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="notification",
            name="target",
            field=models.CharField(
                choices=[
                    ("entry_detail", "Detalle entrada"),
                    ("entries_list", "Lista entradas"),
                    ("exit_detail", "Detalle salida"),
                    ("exits_list", "Lista salidas"),
                    ("bins_list", "Lista bins"),
                    ("position_detail", "Detalle posición"),
                    ("positions_list", "Lista posiciones"),
                    ("cavas_list", "Lista cavas"),
                ],
                max_length=32,
            ),
        ),
    ]
