from django.conf import settings
from django.db import models


class Notification(models.Model):
    class Event(models.TextChoices):
        CREATED = "created", "Creado"
        UPDATED = "updated", "Actualizado"
        DELETED = "deleted", "Eliminado"
        STATUS = "status", "Estado"

    class Artifact(models.TextChoices):
        ENTRY = "entry", "Entrada"
        EXIT = "exit", "Salida"
        BIN = "bin", "Bin"
        POSITION = "position", "Posición"

    class Target(models.TextChoices):
        ENTRY_DETAIL = "entry_detail", "Detalle entrada"
        ENTRIES_LIST = "entries_list", "Lista entradas"
        EXIT_DETAIL = "exit_detail", "Detalle salida"
        EXITS_LIST = "exits_list", "Lista salidas"
        BINS_LIST = "bins_list", "Lista bins"
        POSITION_DETAIL = "position_detail", "Detalle posición"
        POSITIONS_LIST = "positions_list", "Lista posiciones"
        CAVAS_LIST = "cavas_list", "Lista cavas"

    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name="workspace",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    event = models.CharField(max_length=32, choices=Event.choices)
    artifact = models.CharField(
        max_length=32,
        choices=Artifact.choices,
        blank=True,
        default="",
    )
    target = models.CharField(max_length=32, choices=Target.choices)
    message = models.CharField(max_length=512)
    actor_display = models.CharField(max_length=150, blank=True, default="")
    resource_id = models.PositiveIntegerField(null=True, blank=True)
    resource_code = models.CharField(max_length=64, blank=True, default="")
    affected_count = models.PositiveIntegerField(default=1)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "-created_at"]),
            models.Index(fields=["recipient", "read_at"]),
            models.Index(fields=["workspace", "-created_at"]),
        ]
        verbose_name = "notificación"
        verbose_name_plural = "notificaciones"

    def __str__(self) -> str:
        return f"{self.recipient_id} · {self.event} · {self.message[:40]}"
