# Unifica inventario_inicial / conteo_ciclico / inventario_general → control_inventario.

from django.db import migrations, models


LEGACY_TYPES = (
    "inventario_inicial",
    "conteo_ciclico",
    "inventario_general",
    # Tipos previos al rediseño WMS (por si quedara alguno).
    "auditoria",
    "vencimientos",
    "correccion",
    "entrada_almacenamiento",
    "salida_despacho",
)


def forwards_unify_task_type(apps, schema_editor):
    Task = apps.get_model("task", "Task")
    Task.objects.filter(task_type__in=LEGACY_TYPES).update(
        task_type="control_inventario"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("task", "0005_alter_task_options_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards_unify_task_type, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="task",
            name="task_type",
            field=models.CharField(
                choices=[("control_inventario", "Control de inventario")],
                max_length=30,
                verbose_name="tipo de tarea",
            ),
        ),
    ]
