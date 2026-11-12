"""Orquestación de trabajos masivos: 1 ítem sync, >1 en cola Celery."""

from __future__ import annotations

import logging
from typing import Any, Callable

from apps.common.models import BulkOperationJob

logger = logging.getLogger(__name__)

# Más de este umbral → Celery (regla: masivo > 1).
ASYNC_MIN_ITEMS = 2

USER_DETAIL_ASYNC = "En proceso. Puedes continuar mientras se procesa."


def serialize_bulk_job(job: BulkOperationJob, *, async_: bool | None = None) -> dict[str, Any]:
    result = job.result or {}
    is_async = (
        async_
        if async_ is not None
        else job.status
        in (
            BulkOperationJob.Status.PENDING,
            BulkOperationJob.Status.RUNNING,
        )
    )
    payload: dict[str, Any] = {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "async": is_async,
        "count": len(job.item_ids or []),
        "result": result,
        "detail": (
            USER_DETAIL_ASYNC
            if job.status
            in (
                BulkOperationJob.Status.PENDING,
                BulkOperationJob.Status.RUNNING,
            )
            else (result.get("message") or job.error_message or "")
        ),
    }
    # Compat con respuestas sync actuales del front.
    if job.status == BulkOperationJob.Status.SUCCEEDED:
        payload["success"] = True
        payload["message"] = result.get("message") or "Listo."
        for key in (
            "printed",
            "skipped",
            "deleted",
            "blocked",
            "regenerated",
            "failed",
        ):
            if key in result:
                payload[key] = result[key]
    elif job.status == BulkOperationJob.Status.FAILED:
        payload["success"] = False
        payload["message"] = job.error_message or "No se pudo completar la operación."
    else:
        payload["success"] = True
        payload["message"] = USER_DETAIL_ASYNC
    return payload


def create_bulk_job(
    *,
    workspace,
    kind: str,
    item_ids: list[int],
    params: dict[str, Any] | None = None,
) -> BulkOperationJob:
    initial: dict[str, Any] = {}
    if params:
        initial["params"] = params
    return BulkOperationJob.objects.create(
        workspace=workspace,
        kind=kind,
        item_ids=[int(x) for x in item_ids],
        status=BulkOperationJob.Status.PENDING,
        result=initial,
    )


def execute_bulk_job(
    job_id: int,
    runner: Callable[[BulkOperationJob], dict[str, Any]],
) -> BulkOperationJob:
    job = BulkOperationJob.objects.filter(pk=job_id).first()
    if job is None:
        return job
    if job.status == BulkOperationJob.Status.SUCCEEDED:
        return job

    job.status = BulkOperationJob.Status.RUNNING
    job.save(update_fields=["status", "updated_at"])
    try:
        result = runner(job) or {}
    except Exception as exc:
        logger.exception("Bulk job %s (%s) falló", job_id, job.kind)
        job.status = BulkOperationJob.Status.FAILED
        job.error_message = str(exc)[:500] or "No se pudo completar la operación."
        job.save(update_fields=["status", "error_message", "updated_at"])
        return job

    job.result = result
    job.status = BulkOperationJob.Status.SUCCEEDED
    job.error_message = ""
    job.save(update_fields=["result", "status", "error_message", "updated_at"])
    try:
        from apps.notifications.services import notify_bulk_job_finished

        notify_bulk_job_finished(job)
    except Exception:
        logger.exception("No se pudo notificar bulk job %s", job_id)
    return job


def start_bulk_operation(
    *,
    workspace,
    kind: str,
    item_ids: list[int],
    runner: Callable[[BulkOperationJob], dict[str, Any]],
    celery_task,
    params: dict[str, Any] | None = None,
) -> tuple[BulkOperationJob, bool]:
    """
    Devuelve (job, ran_async).
    ran_async=False → ya terminó en este request.
    """
    ids = [int(x) for x in item_ids]
    job = create_bulk_job(
        workspace=workspace, kind=kind, item_ids=ids, params=params
    )
    if len(ids) < ASYNC_MIN_ITEMS:
        execute_bulk_job(job.id, runner)
        job.refresh_from_db()
        return job, False

    try:
        celery_task.delay(job.id)
    except Exception:
        logger.exception("No se pudo encolar bulk job %s", job.id)
        # Fallback sync si no hay worker/broker.
        execute_bulk_job(job.id, runner)
        job.refresh_from_db()
        return job, False

    return job, True
