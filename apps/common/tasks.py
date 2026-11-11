import logging

from celery import shared_task

from apps.common.models import BulkOperationJob
from apps.common.utils.bulk_jobs import execute_bulk_job

logger = logging.getLogger(__name__)


def _runner_for(kind: str):
    from apps.inventory.services_bulk import (
        run_bin_delete,
        run_bin_print,
        run_bin_regenerate_tags,
    )
    from apps.storage.services_bulk import (
        run_position_delete,
        run_position_print,
        run_position_regenerate_tags,
    )

    return {
        BulkOperationJob.Kind.BIN_REGENERATE_TAGS: run_bin_regenerate_tags,
        BulkOperationJob.Kind.BIN_PRINT: run_bin_print,
        BulkOperationJob.Kind.BIN_DELETE: run_bin_delete,
        BulkOperationJob.Kind.POSITION_REGENERATE_TAGS: run_position_regenerate_tags,
        BulkOperationJob.Kind.POSITION_PRINT: run_position_print,
        BulkOperationJob.Kind.POSITION_DELETE: run_position_delete,
    }.get(kind)


@shared_task
def run_bulk_operation_job(job_id: int) -> None:
    job = BulkOperationJob.objects.filter(pk=job_id).first()
    if job is None:
        return
    runner = _runner_for(job.kind)
    if runner is None:
        logger.error("Bulk job %s con kind desconocido: %s", job_id, job.kind)
        job.status = BulkOperationJob.Status.FAILED
        job.error_message = "Tipo de operación no soportado."
        job.save(update_fields=["status", "error_message", "updated_at"])
        return
    execute_bulk_job(job_id, runner)
