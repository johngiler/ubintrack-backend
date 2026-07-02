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
