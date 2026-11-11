from django.db import models
from django_currentuser.db.models import CurrentUserField


class BaseModel(models.Model):
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="activo",
        help_text="Si está desmarcado, el registro se considera inactivo (no borrado).",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="fecha de creación",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="fecha de actualización",
    )
    created_by = CurrentUserField(
        related_name="%(app_label)s_%(class)s_creates",
        verbose_name="creado por",
    )
    updated_by = CurrentUserField(
        related_name="%(app_label)s_%(class)s_updates",
        on_update=True,
        verbose_name="actualizado por",
    )

    class Meta:
        abstract = True


class BulkOperationJob(BaseModel):
    """Trabajos masivos (print / regen / delete) — async si hay más de un ítem."""

    class Kind(models.TextChoices):
        BIN_REGENERATE_TAGS = "bin_regenerate_tags", "Bins: regenerar etiquetas"
        BIN_PRINT = "bin_print", "Bins: imprimir"
        BIN_DELETE = "bin_delete", "Bins: eliminar"
        POSITION_REGENERATE_TAGS = (
            "position_regenerate_tags",
            "Posiciones: regenerar QR",
        )
        POSITION_PRINT = "position_print", "Posiciones: imprimir"
        POSITION_DELETE = "position_delete", "Posiciones: eliminar"

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        RUNNING = "running", "En proceso"
        SUCCEEDED = "succeeded", "Listo"
        FAILED = "failed", "Fallido"

    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="bulk_operation_jobs",
        verbose_name="workspace",
    )
    kind = models.CharField(
        max_length=40,
        choices=Kind.choices,
        verbose_name="tipo",
        db_index=True,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="estado",
        db_index=True,
    )
    item_ids = models.JSONField(default=list, verbose_name="ids")
    result = models.JSONField(default=dict, blank=True, verbose_name="resultado")
    error_message = models.TextField(blank=True, default="", verbose_name="error")

    class Meta:
        verbose_name = "trabajo masivo"
        verbose_name_plural = "trabajos masivos"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind} {self.status} ({len(self.item_ids or [])})"
