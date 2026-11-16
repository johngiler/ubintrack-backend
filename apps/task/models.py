from django.db import models
from django.db.models import Q

from apps.common.models import BaseModel
from apps.core.choices import (
    TaskLineStatus,
    TaskPriority,
    TaskStatus,
    TaskType,
    UserType,
)
from apps.core.models import CustomUser

_STAFF_USER_FILTER = ~Q(user_type=UserType.CLIENT)


class Task(BaseModel):
    """
    Orden de trabajo de bodega (inventario inicial / cíclico / general).
    Alcance: un cliente; una/varias/todas las cámaras; pasillos listados;
    uno/varios/todos los productos. Vacío en M2M/listas = sin filtrar (todos).
    Las líneas (TaskLine) materializan posiciones/bins a visitar.
    """

    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="tasks",
        verbose_name="workspace",
    )
    code = models.CharField(
        max_length=100,
        unique=True,
        editable=False,
        verbose_name="código de tarea",
    )
    title = models.CharField(
        max_length=200,
        blank=True,
        default="",
        verbose_name="título",
    )
    task_type = models.CharField(
        max_length=30,
        choices=TaskType.choices,
        verbose_name="tipo de tarea",
    )
    status = models.CharField(
        max_length=20,
        choices=TaskStatus.choices,
        default=TaskStatus.PENDING,
        verbose_name="estado",
    )
    priority = models.CharField(
        max_length=10,
        choices=TaskPriority.choices,
        default=TaskPriority.MEDIUM,
        verbose_name="prioridad",
    )
    started_at = models.DateTimeField(
        null=True, blank=True, verbose_name="fecha de inicio"
    )
    completed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="fecha de fin"
    )

    assigned_to = models.ManyToManyField(
        CustomUser,
        blank=True,
        related_name="tasks",
        limit_choices_to=_STAFF_USER_FILTER,
        verbose_name="asignado a",
    )

    client = models.ForeignKey(
        "core.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
        verbose_name="cliente",
    )
    cavas = models.ManyToManyField(
        "storage.Cava",
        blank=True,
        related_name="tasks",
        verbose_name="cámaras",
    )
    halls = models.JSONField(
        default=list,
        blank=True,
        verbose_name="pasillos",
        help_text="Tokens de pasillo/túnel (P1, T1, …). Vacío = todos.",
    )
    products = models.ManyToManyField(
        "inventory.Product",
        blank=True,
        related_name="tasks",
        verbose_name="productos",
    )
    include_empty_positions = models.BooleanField(
        default=False,
        verbose_name="incluir posiciones vacías",
        help_text="Inventario general: también visitar ubicaciones sin bin.",
    )
    require_approval = models.BooleanField(
        default=False,
        verbose_name="requiere aprobación",
        help_text="Inventario general: cierre con verificación de supervisor.",
    )

    observations = models.TextField(
        null=True, blank=True, verbose_name="observaciones"
    )

    class Meta:
        verbose_name = "tarea"
        verbose_name_plural = "tareas"
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        if not self.code:
            if self.workspace_id:
                from apps.workspaces.models import Workspace

                slug = (
                    Workspace.objects.filter(pk=self.workspace_id)
                    .values_list("slug", flat=True)
                    .first()
                    or "TASK"
                )
                prefix = f'{slug.upper().replace("-", "")}-TASK'
                last_task = (
                    Task.objects.filter(workspace_id=self.workspace_id)
                    .order_by("id")
                    .last()
                )
            else:
                prefix = "TASK"
                last_task = Task.objects.filter(workspace__isnull=True).order_by(
                    "id"
                ).last()

            if last_task and last_task.code and "-" in last_task.code:
                try:
                    new_number = int(last_task.code.split("-")[-1]) + 1
                except ValueError:
                    new_number = 1
            else:
                new_number = 1
            self.code = f"{prefix}-{new_number:010d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class TaskLine(BaseModel):
    """Unidad a visitar dentro de una tarea (posición y, si aplica, bin)."""

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="lines",
        verbose_name="tarea",
    )
    position = models.ForeignKey(
        "storage.Position",
        on_delete=models.CASCADE,
        related_name="task_lines",
        verbose_name="posición",
    )
    bin = models.ForeignKey(
        "inventory.Bin",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_lines",
        verbose_name="bin",
    )
    line_status = models.CharField(
        max_length=20,
        choices=TaskLineStatus.choices,
        default=TaskLineStatus.PENDING,
        verbose_name="estado de línea",
    )
    sort_order = models.PositiveIntegerField(default=0, verbose_name="orden")
    notes = models.TextField(blank=True, default="", verbose_name="notas")

    class Meta:
        verbose_name = "línea de tarea"
        verbose_name_plural = "líneas de tarea"
        ordering = ("sort_order", "id")
        indexes = [
            models.Index(fields=["task", "line_status"]),
        ]

    def __str__(self):
        return f"{self.task_id}:{self.position_id}"


class TaskLog(BaseModel):
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="task_logs",
        verbose_name="tarea",
    )
    message = models.TextField(editable=False, verbose_name="mensaje")

    class Meta:
        verbose_name = "log de tarea"
        verbose_name_plural = "logs de tarea"

    def __str__(self):
        return self.task.code
