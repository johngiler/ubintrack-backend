import logging

from celery import shared_task

from apps.storage.generation import generate_positions_for_cava
from apps.storage.models import CavaGenerationJob

logger = logging.getLogger(__name__)


@shared_task
def generate_cava_positions(job_id: int) -> None:
    job = (
        CavaGenerationJob.objects.select_related(
            "cava", "cava__storage", "cava__storage__workspace", "created_by"
        )
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
    try:
        from apps.audit.models import ActivityBatch, ActivitySection
        from apps.audit.services.view_log import log_bulk
        from apps.notifications.services import notify_positions_generated

        notify_positions_generated(
            cava=job.cava,
            created_count=result["created"],
            actor=job.created_by,
        )
        items = result.get("created_items") or []
        if items:
            cava_name = job.cava.name or job.cava.code or str(job.cava_id)
            actor = job.created_by
            actor_name = (
                (getattr(actor, "get_full_name", lambda: "")() or "").strip()
                if actor
                else "Sistema"
            ) or (getattr(actor, "username", None) if actor else "Sistema") or "Sistema"
            noun = "posición" if len(items) == 1 else "posiciones"
            log_bulk(
                workspace=job.workspace,
                section=ActivitySection.POSITION,
                action="generate",
                kind=ActivityBatch.Kind.GENERATE,
                summary=f"{actor_name} generó {len(items)} {noun} en {cava_name}",
                items=items,
                actor=actor,
            )
    except Exception:
        logger.exception(
            "No se pudo notificar generación de cava %s", job.cava_id
        )
