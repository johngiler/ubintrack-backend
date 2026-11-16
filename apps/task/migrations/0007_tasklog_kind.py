from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("task", "0006_unify_task_type_control_inventario"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="tasklog",
            options={
                "ordering": ("created_at", "id"),
                "verbose_name": "log de tarea",
                "verbose_name_plural": "logs de tarea",
            },
        ),
        migrations.AddField(
            model_name="tasklog",
            name="kind",
            field=models.CharField(
                choices=[
                    ("workflow", "Movimiento"),
                    ("scope", "Alcance"),
                    ("meta", "Meta"),
                ],
                db_index=True,
                default="meta",
                max_length=16,
                verbose_name="tipo",
            ),
        ),
    ]
