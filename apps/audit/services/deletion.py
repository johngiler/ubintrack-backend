"""Guard so audit history rows are only deleted via Django admin (superuser)."""

from __future__ import annotations

from contextlib import contextmanager
from threading import local

from django.db import models
from django.db.models.deletion import ProtectedError

_state = local()

_AUDIT_DELETE_MSG = (
    "Los registros de auditoría no se pueden eliminar desde la aplicación. "
    "Solo un superusuario en Django Admin puede borrarlos."
)


def audit_deletion_allowed() -> bool:
    return bool(getattr(_state, "allowed", False))


@contextmanager
def allow_audit_deletion():
    previous = getattr(_state, "allowed", False)
    _state.allowed = True
    try:
        yield
    finally:
        _state.allowed = previous


class ImmutableAuditQuerySet(models.QuerySet):
    def delete(self):
        if not audit_deletion_allowed():
            raise ProtectedError(_AUDIT_DELETE_MSG, list(self[:50]))
        return super().delete()


class ImmutableAuditManager(models.Manager.from_queryset(ImmutableAuditQuerySet)):
    pass


class ImmutableAuditModel(models.Model):
    objects = ImmutableAuditManager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        if not audit_deletion_allowed():
            raise ProtectedError(_AUDIT_DELETE_MSG, [self])
        return super().delete(using=using, keep_parents=keep_parents)
