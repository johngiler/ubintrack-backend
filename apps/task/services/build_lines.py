"""Materializa el alcance de una tarea de control de inventario en TaskLines."""

from __future__ import annotations

from django.db.models import Prefetch, Q, Sum

from apps.core.choices import BinStatus, TaskLineStatus, TaskType
from apps.inventory.models import Bin, BinProduct
from apps.task import models as task_models


def _hall_filter(halls: list[str]) -> Q:
    """P1 también incluye compuestos tipo P1-P2 (piso entre secciones)."""
    q = Q()
    for raw in halls:
        h = str(raw or "").strip()
        if not h:
            continue
        q |= (
            Q(position__hall=h)
            | Q(position__hall__startswith=f"{h}-")
            | Q(position__hall__endswith=f"-{h}")
            | Q(position__hall__contains=f"-{h}-")
        )
    return q


def _scoped_bins(task: task_models.Task):
    qs = (
        Bin.objects.filter(
            workspace_id=task.workspace_id,
            status=BinStatus.ALMACENADO,
            position__isnull=False,
            is_active=True,
        )
        .select_related("position", "position__cava", "client")
        .prefetch_related(
            Prefetch(
                "bin_products",
                queryset=BinProduct.objects.filter(is_active=True).select_related(
                    "entry_product__product"
                ),
            )
        )
        .order_by("position__code", "id")
    )
    if task.client_id:
        qs = qs.filter(client_id=task.client_id)

    cava_ids = list(task.cavas.values_list("id", flat=True))
    if cava_ids:
        qs = qs.filter(position__cava_id__in=cava_ids)

    halls = [str(h).strip() for h in (task.halls or []) if str(h).strip()]
    if halls:
        hall_q = _hall_filter(halls)
        if hall_q:
            qs = qs.filter(hall_q)

    product_ids = list(task.products.values_list("id", flat=True))
    if product_ids:
        qs = qs.filter(
            bin_products__entry_product__product_id__in=product_ids,
            bin_products__is_active=True,
        ).distinct()

    return qs


def scoped_bins_count(task: task_models.Task) -> int:
    return _scoped_bins(task).count()


def build_task_lines(task: task_models.Task, *, force: bool = False) -> int:
    """
    Crea TaskLines (1 bin almacenado por posición).
    - Sin líneas → crea.
    - Solo pendientes + force → borra y recrea.
    - Con progreso → no toca (salvo force, que también recrea).
    Returns cantidad de líneas creadas (0 si no tocó o alcance vacío).
    """
    if task.task_type != TaskType.CONTROL_INVENTARIO:
        return 0

    existing = task.lines.all()
    if existing.exists():
        has_progress = existing.exclude(line_status=TaskLineStatus.PENDING).exists()
        if has_progress and not force:
            return 0
        if not force:
            return 0
        existing.delete()

    bins = list(_scoped_bins(task))
    # 1 bin por posición (primera por code/id).
    by_position = {}
    for bin_obj in bins:
        pid = bin_obj.position_id
        if pid and pid not in by_position:
            by_position[pid] = bin_obj

    lines = []
    for order, bin_obj in enumerate(by_position.values()):
        lines.append(
            task_models.TaskLine(
                task=task,
                position_id=bin_obj.position_id,
                bin=bin_obj,
                line_status=TaskLineStatus.PENDING,
                sort_order=order,
            )
        )
    if lines:
        task_models.TaskLine.objects.bulk_create(lines)
    return len(lines)


def bin_system_quantity(bin_obj: Bin | None) -> int:
    if bin_obj is None:
        return 0
    total = (
        bin_obj.bin_products.filter(is_active=True).aggregate(total=Sum("quantity"))[
            "total"
        ]
        or 0
    )
    return int(total)
