"""Paquete de tareas Celery de workspaces; implementación en :mod:`apps.workspaces.tasks.workspace_tasks`."""

from apps.workspaces.tasks.workspace_tasks import workspace_smtp_connection_test_task

__all__ = ["workspace_smtp_connection_test_task"]
