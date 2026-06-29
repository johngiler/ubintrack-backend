from django.db import models
from django.utils import timezone


class TimeStampedActiveModel(models.Model):
    """
    Campos comunes: auditoría y bandera de registro activo.
    Heredar en modelos de dominio (no en tablas de unión puras si no aplica).

    Usa default explícito (no auto_now) para migraciones sin datos interactivos en filas existentes.
    """

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Si está desmarcado, el registro se considera inactivo (no borrado).",
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk is not None:
            self.updated_at = timezone.now()
        super().save(*args, **kwargs)
