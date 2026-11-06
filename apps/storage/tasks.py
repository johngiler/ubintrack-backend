import logging

from celery import shared_task

from apps.storage.generation import generate_positions_for_cava
from apps.storage.models import CavaGenerationJob

logger = logging.getLogger(__name__)


@shared_task
def generate_cava_positions(job_id: int) -> None:
    job = (
        CavaGenerationJob.objects.select_related("cava", "cava__storage")
        .filter(pk=job_id)
        .first()
    )
    if job is None or job.status == CavaGenerationJob.Status.SUCCEEDED:
        return

    job.status = CavaGenerationJob.Status.RUNNING
    job.save(update_fields=["status", "updated_at"])
    try:
        result = generate_positions_for_cava(job.cava)
    except Exception:
        logger.exception("No se pudieron crear las posiciones de la cava %s", job.cava_id)
        job.status = CavaGenerationJob.Status.FAILED
        job.save(update_fields=["status", "updated_at"])
        return

    job.status = CavaGenerationJob.Status.SUCCEEDED
    job.expected_count = result["expected"]
    job.created_count = result["created"]
    job.skipped_count = result["skipped"]
    job.save(
        update_fields=[
            "status",
            "expected_count",
            "created_count",
            "skipped_count",
            "updated_at",
        ]
    )
