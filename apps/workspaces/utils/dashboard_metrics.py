"""
Métricas extendidas del resumen admin (workspace). Solo lo calculable con datos existentes.
"""

from __future__ import annotations

from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from apps.ad_spaces.models import AdSpaceFormat
from apps.availability.models import AvailabilityBlock
from apps.orders.models import Order, OrderItem, OrderStatus, OrderStatusEvent


def _fdec(x) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _start_of_quarter(d: date) -> date:
    m = (d.month - 1) // 3 * 3 + 1
    return date(d.year, m, 1)


def _month_series_last_n_months(today: date, n: int) -> list[tuple[date, date]]:
    """(first_day, last_day) por mes, del más antiguo al más reciente."""
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        last = date(y, m, monthrange(y, m)[1])
        first = date(y, m, 1)
        out.append((first, last))
        if m == 1:
            y -= 1
            m = 12
        else:
            m -= 1
    out.reverse()
    return out


def build_extended_metrics(*, ws, orders_qs, spaces_qs) -> dict:
    today = timezone.localdate()

    n_spaces = spaces_qs.count()
    if n_spaces == 0 and not orders_qs.exists():
        return _empty_metrics()

    # —— Contrato vigente hoy (líneas en pedido activo) ——
    under_today = OrderItem.objects.filter(
        order__client__workspace=ws,
        order__status=OrderStatus.ACTIVE,
        start_date__lte=today,
        end_date__gte=today,
    )
    n_under_contract_today = under_today.values("ad_space_id").distinct().count()
    monthly_real_usd = under_today.aggregate(s=Sum("monthly_price"))["s"] or Decimal("0")

    capacity_listed = spaces_qs.aggregate(s=Sum("monthly_price_usd"))["s"] or Decimal("0")
    occ_pct = (
        round(100.0 * n_under_contract_today / n_spaces, 1) if n_spaces else None
    )
    cap_use_pct = (
        round(100.0 * float(monthly_real_usd) / float(capacity_listed), 1)
        if capacity_listed and capacity_listed > 0
        else None
    )

    # —— Ingreso contratado (total pedido) por periodo: pedidos activos o vencidos ——
    rev_base = orders_qs.filter(status__in=(OrderStatus.ACTIVE, OrderStatus.EXPIRED))

    def revenue_since(d0: date) -> Decimal:
        dt0 = timezone.make_aware(datetime.combine(d0, datetime.min.time()))
        return (
            rev_base.filter(
                Q(submitted_at__gte=dt0) | Q(submitted_at__isnull=True, created_at__gte=dt0)
            ).aggregate(s=Sum("total_amount"))["s"]
            or Decimal("0")
        )

    first_month = date(today.year, today.month, 1)
    first_q = _start_of_quarter(today)
    first_year = date(today.year, 1, 1)
    revenue_mtd = revenue_since(first_month)
    revenue_qtd = revenue_since(first_q)
    revenue_ytd = revenue_since(first_year)

    # —— Serie mensual ingresos (12 meses, pedidos activos+vencidos con submitted/created en el mes) ——
    months = _month_series_last_n_months(today, 12)
    revenue_by_month = []
    for first, last in months:
        dt0 = timezone.make_aware(datetime.combine(first, datetime.min.time()))
        dt1 = timezone.make_aware(datetime.combine(last + timedelta(days=1), datetime.min.time()))
        s = (
            rev_base.filter(
                Q(submitted_at__gte=dt0, submitted_at__lt=dt1)
                | Q(submitted_at__isnull=True, created_at__gte=dt0, created_at__lt=dt1)
            ).aggregate(x=Sum("total_amount"))["x"]
            or Decimal("0")
        )
        revenue_by_month.append({"month": first.isoformat()[:7], "total_usd": _fdec(s)})

    # —— Pedidos nuevos por mes (enviados o más allá, no borrador) ——
    pipeline_orders = orders_qs.exclude(status=OrderStatus.DRAFT)
    new_by_month = []
    for first, last in months:
        dt0 = timezone.make_aware(datetime.combine(first, datetime.min.time()))
        dt1 = timezone.make_aware(datetime.combine(last + timedelta(days=1), datetime.min.time()))
        c = pipeline_orders.filter(
            Q(submitted_at__gte=dt0, submitted_at__lt=dt1)
            | Q(submitted_at__isnull=True, created_at__gte=dt0, created_at__lt=dt1)
        ).count()
        new_by_month.append({"month": first.isoformat()[:7], "count": c})

    # —— Toma más rentable (suma subtotales líneas en pedidos activos/vencidos) ——
    top_space = (
        OrderItem.objects.filter(
            order__client__workspace=ws,
            order__status__in=(OrderStatus.ACTIVE, OrderStatus.EXPIRED),
        )
        .values("ad_space_id", "ad_space__code", "ad_space__name")
        .annotate(revenue=Sum("subtotal"))
        .order_by("-revenue")
        .first()
    )
    top_space_out = None
    if top_space:
        top_space_out = {
            "ad_space_id": top_space["ad_space_id"],
            "code": top_space["ad_space__code"],
            "title": top_space["ad_space__name"],
            "name": top_space["ad_space__name"],
            "revenue_usd": _fdec(top_space["revenue"]),
        }

    # —— Menos demandada: días sin contrato activo desde último fin (o desde alta de toma) ——
    space_ids = list(spaces_qs.values_list("id", flat=True))
    last_end_map = {
        r["ad_space_id"]: r["m"]
        for r in OrderItem.objects.filter(
            order__client__workspace=ws,
            order__status__in=(OrderStatus.ACTIVE, OrderStatus.EXPIRED),
        )
        .values("ad_space_id")
        .annotate(m=Max("end_date"))
    }
    space_meta = {
        s["id"]: s
        for s in spaces_qs.values("id", "created_at", "code", "name")
    }
    under_ids = set(under_today.values_list("ad_space_id", flat=True))
    idle_rows = []
    for sid in space_ids:
        sp = space_meta.get(sid)
        if not sp:
            continue
        le = last_end_map.get(sid)
        created_d = sp["created_at"].date() if hasattr(sp["created_at"], "date") else today
        if sid in under_ids:
            idle = 0
        elif le is None:
            idle = max(0, (today - created_d).days)
        else:
            idle = max(0, (today - le).days)
        idle_rows.append(
            {
                "ad_space_id": sid,
                "code": sp["code"],
                "title": sp["name"],
                "name": sp["name"],
                "idle_days": idle,
                "last_contract_end": le.isoformat() if le else None,
            }
        )
    idle_rows.sort(key=lambda x: -x["idle_days"])
    coldest = idle_rows[:5]
    avg_idle = (
        round(sum(r["idle_days"] for r in idle_rows) / len(idle_rows), 1) if idle_rows else None
    )

    # —— Vencimientos próximos 30 días (líneas en pedido activo) ——
    soon_end = today + timedelta(days=30)
    n_ending_30d = OrderItem.objects.filter(
        order__client__workspace=ws,
        order__status=OrderStatus.ACTIVE,
        end_date__gte=today,
        end_date__lte=soon_end,
    ).count()

    # —— Por tipo de elemento (product_type en líneas de formato) ——
    spaces_by_type = [
        {
            "type": (row["product_type__slug"] or "").strip(),
            "label": (row["product_type__name"] or "").strip(),
            "count": row["c"],
        }
        for row in (
            AdSpaceFormat.objects.filter(ad_space__in=spaces_qs)
            .values("product_type__slug", "product_type__name")
            .annotate(c=Count("ad_space_id", distinct=True))
            .order_by("-c", "product_type__name")
        )
        if (row["product_type__slug"] or row["product_type__name"])
    ]

    # —— Canceladas y etapa previa (from_status del último paso a cancelada) ——
    n_cancelled = orders_qs.filter(status=OrderStatus.CANCELLED).count()
    cancelled_from = [
        {"from_status": row["from_status"] or "—", "count": row["c"]}
        for row in OrderStatusEvent.objects.filter(
            order__client__workspace=ws,
            to_status=OrderStatus.CANCELLED,
        )
        .values("from_status")
        .annotate(c=Count("id"))
        .order_by("-c")
    ]

    # —— Bloqueos manuales activos ——
    n_active_blocks = AvailabilityBlock.objects.filter(
        ad_space__shopping_center__workspace=ws,
        is_active=True,
    ).count()

    def avg_days_between(from_s: str, to_s: str) -> float | None:
        """Promedio días entre el primer evento `from_s` y el primer `to_s` posterior (misma orden)."""
        pairs = []
        by_order: dict[int, list] = {}
        for ev in OrderStatusEvent.objects.filter(order__client__workspace=ws).order_by(
            "order_id", "created_at", "id"
        ).iterator(chunk_size=800):
            by_order.setdefault(ev.order_id, []).append(ev)
        for lst in by_order.values():
            t_from = None
            t_to = None
            for ev in lst:
                if t_from is None and ev.to_status == from_s:
                    t_from = ev.created_at
                elif t_from is not None and ev.to_status == to_s:
                    t_to = ev.created_at
                    break
            if t_from and t_to and t_to >= t_from:
                pairs.append((t_to - t_from).total_seconds() / 86400.0)
        if not pairs:
            return None
        return round(sum(pairs) / len(pairs), 2)

    avg_days_submitted_to_active = None
    # submitted_at → primera transición a active
    sa_pairs = []
    for o in rev_base.exclude(submitted_at__isnull=True).iterator(chunk_size=200):
        fa = (
            OrderStatusEvent.objects.filter(order_id=o.pk, to_status=OrderStatus.ACTIVE)
            .order_by("created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        if fa and fa >= o.submitted_at:
            sa_pairs.append((fa - o.submitted_at).total_seconds() / 86400.0)
    if sa_pairs:
        avg_days_submitted_to_active = round(sum(sa_pairs) / len(sa_pairs), 2)

    avg_days_client_to_art = avg_days_between(
        OrderStatus.CLIENT_APPROVED, OrderStatus.ART_APPROVED
    )
    avg_days_invoice_to_paid = avg_days_between(OrderStatus.INVOICED, OrderStatus.PAID)

    # —— Permiso alcaldía: pedidos en ese estado por ciudad del centro (primera línea) ——
    permit_by_city = []
    permit_qs = Order.objects.filter(
        client__workspace=ws, status=OrderStatus.PERMIT_PENDING
    ).prefetch_related("items__ad_space__shopping_center")

    city_counts = Counter()
    for o in permit_qs.iterator(chunk_size=100):
        lines = list(o.items.all()[:1])
        it = lines[0] if lines else None
        city = ""
        if it is not None and it.ad_space_id:
            sc = getattr(it.ad_space, "shopping_center", None)
            city = (getattr(sc, "city", None) or "").strip()
        city_counts[city or "Sin ciudad"] += 1
    permit_by_city = [{"city": k, "count": v} for k, v in city_counts.most_common(12)]

    return {
        "occupancy": {
            "spaces_total": n_spaces,
            "spaces_under_contract_today": n_under_contract_today,
            "occupancy_contract_pct": occ_pct,
            "monthly_revenue_contract_usd": _fdec(monthly_real_usd),
            "monthly_capacity_listed_usd": _fdec(capacity_listed),
            "capacity_use_pct": cap_use_pct,
        },
        "revenue_periods": {
            "mtd_usd": _fdec(revenue_mtd),
            "quarter_usd": _fdec(revenue_qtd),
            "ytd_usd": _fdec(revenue_ytd),
        },
        "revenue_by_month": revenue_by_month,
        "new_orders_by_month": new_by_month,
        "top_space_by_revenue": top_space_out,
        "coldest_spaces": coldest,
        "avg_idle_days_per_space": avg_idle,
        "renewal_rate_pct": None,
        "renewal_note": "No hay marca de renovación en el modelo; métrica pendiente.",
        "lines_ending_within_30_days": n_ending_30d,
        "spaces_by_type": spaces_by_type,
        "avg_days_submitted_to_active": avg_days_submitted_to_active,
        "avg_days_client_approved_to_art_approved": avg_days_client_to_art,
        "avg_days_invoiced_to_paid": avg_days_invoice_to_paid,
        "permit_pending_by_city": permit_by_city,
        "orders_cancelled_total": n_cancelled,
        "orders_cancelled_from_status": cancelled_from,
        "active_availability_blocks": n_active_blocks,
    }


def _empty_metrics():
    return {
        "occupancy": {
            "spaces_total": 0,
            "spaces_under_contract_today": 0,
            "occupancy_contract_pct": None,
            "monthly_revenue_contract_usd": None,
            "monthly_capacity_listed_usd": None,
            "capacity_use_pct": None,
        },
        "revenue_periods": {"mtd_usd": None, "quarter_usd": None, "ytd_usd": None},
        "revenue_by_month": [],
        "new_orders_by_month": [],
        "top_space_by_revenue": None,
        "coldest_spaces": [],
        "avg_idle_days_per_space": None,
        "renewal_rate_pct": None,
        "renewal_note": "No hay marca de renovación en el modelo; métrica pendiente.",
        "lines_ending_within_30_days": 0,
        "spaces_by_type": [],
        "avg_days_submitted_to_active": None,
        "avg_days_client_approved_to_art_approved": None,
        "avg_days_invoiced_to_paid": None,
        "permit_pending_by_city": [],
        "orders_cancelled_total": 0,
        "orders_cancelled_from_status": [],
        "active_availability_blocks": 0,
    }
