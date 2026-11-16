# Generated manually — módulo de tareas WMS (nunca en prod: se limpia data legacy).

import django.db.models.deletion
import django_currentuser.db.models.fields
import django_currentuser.middleware
from django.conf import settings
from django.db import migrations, models


def wipe_legacy_tasks(apps, schema_editor):
    Task = apps.get_model("task", "Task")
    Task.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_alter_custompermission_module"),
        ("inventory", "0010_bin_label_include_observation"),
        ("storage", "0033_entry_position_movements"),
        ("task", "0002_alter_task_created_at_alter_task_is_active_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(wipe_legacy_tasks, migrations.RunPython.noop),
        migrations.DeleteModel(name="UserTaskTracking"),
        migrations.RemoveField(model_name="task", name="entry"),
        migrations.RemoveField(model_name="task", name="exit"),
        migrations.AddField(
            model_name="task",
            name="title",
            field=models.CharField(
                blank=True, default="", max_length=200, verbose_name="título"
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="client",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tasks",
                to="core.client",
                verbose_name="cliente",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="cava",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tasks",
                to="storage.cava",
                verbose_name="cámara",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="hall",
            field=models.CharField(
                blank=True, default="", max_length=64, verbose_name="pasillo"
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tasks",
                to="inventory.product",
                verbose_name="producto",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="include_empty_positions",
            field=models.BooleanField(
                default=False,
                help_text="Inventario general: también visitar ubicaciones sin bin.",
                verbose_name="incluir posiciones vacías",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="require_approval",
            field=models.BooleanField(
                default=False,
                help_text="Inventario general: cierre con verificación de supervisor.",
                verbose_name="requiere aprobación",
            ),
        ),
        migrations.AlterField(
            model_name="task",
            name="task_type",
            field=models.CharField(
                choices=[
                    ("inventario_inicial", "Inventario inicial"),
                    ("conteo_ciclico", "Conteo cíclico"),
                    ("inventario_general", "Inventario general"),
                ],
                max_length=30,
                verbose_name="tipo de tarea",
            ),
        ),
        migrations.AlterField(
            model_name="task",
            name="priority",
            field=models.CharField(
                choices=[
                    ("low", "Baja"),
                    ("medium", "Media"),
                    ("high", "Alta"),
                ],
                default="medium",
                max_length=10,
                verbose_name="prioridad",
            ),
        ),
        migrations.CreateModel(
            name="TaskLine",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        db_index=True,
                        default=True,
                        help_text="Si está desmarcado, el registro se considera inactivo (no borrado).",
                        verbose_name="activo",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                        verbose_name="fecha de creación",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True, verbose_name="fecha de actualización"
                    ),
                ),
                (
                    "line_status",
                    models.CharField(
                        choices=[
                            ("pendiente", "Pendiente"),
                            ("contado", "Contado"),
                            ("diferencia", "Diferencia"),
                            ("omitido", "Omitido"),
                        ],
                        default="pendiente",
                        max_length=20,
                        verbose_name="estado de línea",
                    ),
                ),
                (
                    "sort_order",
                    models.PositiveIntegerField(default=0, verbose_name="orden"),
                ),
                (
                    "notes",
                    models.TextField(blank=True, default="", verbose_name="notas"),
                ),
                (
                    "bin",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="task_lines",
                        to="inventory.bin",
                        verbose_name="bin",
                    ),
                ),
                (
                    "created_by",
                    django_currentuser.db.models.fields.CurrentUserField(
                        default=django_currentuser.middleware.get_current_authenticated_user,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(app_label)s_%(class)s_creates",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="creado por",
                    ),
                ),
                (
                    "position",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="task_lines",
                        to="storage.position",
                        verbose_name="posición",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="task.task",
                        verbose_name="tarea",
                    ),
                ),
                (
                    "updated_by",
                    django_currentuser.db.models.fields.CurrentUserField(
                        default=django_currentuser.middleware.get_current_authenticated_user,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        on_update=True,
                        related_name="%(app_label)s_%(class)s_updates",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="actualizado por",
                    ),
                ),
            ],
            options={
                "verbose_name": "línea de tarea",
                "verbose_name_plural": "líneas de tarea",
                "ordering": ("sort_order", "id"),
            },
        ),
        migrations.AddIndex(
            model_name="taskline",
            index=models.Index(
                fields=["task", "line_status"], name="task_line_task_status_idx"
            ),
        ),
    ]
