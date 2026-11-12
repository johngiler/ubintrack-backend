from django.conf import settings
from django.db import models

from apps.audit.services.deletion import ImmutableAuditModel


class ActivitySection(models.TextChoices):
    ENTRY = "entry", "Entrada"
    BIN = "bin", "Bin"
    EXIT = "exit", "Salida"
    CAVA = "cava", "Cava"
    POSITION = "position", "Posición"
    PRODUCT = "product", "Producto"
    USER = "user", "Usuario"
    CLIENT = "client", "Cliente"
    PROFILE = "profile", "Perfil"
    TARIFF = "tariff", "Tarifa"


class ActivityBatch(ImmutableAuditModel):
    """Lote de una acción masiva (lista paginable de ítems)."""

    class Kind(models.TextChoices):
        BULK_DELETE = "bulk_delete", "Eliminación masiva"
        BULK_FLAGS = "bulk_flags", "Flags masivos"
        BULK_PRINT = "bulk_print", "Impresión masiva"
        BULK_REGENERATE = "bulk_regenerate", "Regeneración masiva"
        GENERATE = "generate", "Generación"
        OTHER = "other", "Otro"

    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="activity_batches",
    )
    section = models.CharField(max_length=32, choices=ActivitySection.choices, db_index=True)
    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.OTHER)
    summary = models.CharField(max_length=255)
    item_count = models.PositiveIntegerField(default=0)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_batches",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "lote de actividad"
        verbose_name_plural = "lotes de actividad"

    def __str__(self) -> str:
        return f"{self.section} · {self.kind} · {self.item_count}"


class ActivityBatchItem(ImmutableAuditModel):
    batch = models.ForeignKey(
        ActivityBatch,
        on_delete=models.CASCADE,
        related_name="items",
    )
    resource_id = models.PositiveIntegerField(null=True, blank=True)
    resource_code = models.CharField(max_length=64, blank=True, default="")
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["batch", "id"]),
        ]
        verbose_name = "ítem de lote"
        verbose_name_plural = "ítems de lote"

    def __str__(self) -> str:
        return self.resource_code or str(self.resource_id or "?")


class ViewActivityEntry(ImmutableAuditModel):
    """Historial macro por vista (resumen; detalle vive en el ítem)."""

    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="view_activity_entries",
    )
    section = models.CharField(max_length=32, choices=ActivitySection.choices, db_index=True)
    action = models.CharField(max_length=64, db_index=True)
    summary = models.CharField(max_length=255)
    changes = models.JSONField(default=dict, blank=True)
    resource_id = models.PositiveIntegerField(null=True, blank=True)
    resource_code = models.CharField(max_length=64, blank=True, default="")
    affected_count = models.PositiveIntegerField(default=1)
    batch = models.ForeignKey(
        ActivityBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_entries",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="view_activity_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workspace", "section", "-created_at"]),
            models.Index(fields=["section", "action", "-created_at"]),
        ]
        verbose_name = "actividad de vista"
        verbose_name_plural = "actividades de vista"

    def __str__(self) -> str:
        return f"{self.section} · {self.action} · {self.summary[:40]}"
