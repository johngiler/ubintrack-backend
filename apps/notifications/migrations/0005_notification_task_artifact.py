from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0004_alter_notification_artifact"),
    ]

    operations = [
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
                    ("cava", "Cava"),
                    ("task", "Tarea"),
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
                    ("task_detail", "Detalle tarea"),
                    ("tasks_list", "Lista tareas"),
                ],
                max_length=32,
            ),
        ),
    ]
