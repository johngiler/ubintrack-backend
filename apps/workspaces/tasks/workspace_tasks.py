"""Tareas Celery del dominio de workspaces (pruebas SMTP en segundo plano)."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="workspaces.smtp_connection_test")
def workspace_smtp_connection_test_task(
    self,
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    use_tls: bool,
    use_ssl: bool,
) -> dict:
    """
    Ejecuta la prueba de conexión SMTP fuera del worker HTTP.
    Retorna el mismo dict que `run_transactional_smtp_connection_test`.
    """
    from apps.workspaces.utils.smtp_test import run_transactional_smtp_connection_test

    return run_transactional_smtp_connection_test(
        host=host,
        port=port,
        username=username,
        password=password,
        use_tls=use_tls,
        use_ssl=use_ssl,
    )
