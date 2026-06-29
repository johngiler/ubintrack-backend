"""
Agregados para el tab Resumen del panel (gráficos). Filtrado por workspace de la petición.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.db.models import Avg, Count, Max, Min
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ad_spaces.models import AdSpace
from apps.clients.models import Client
from apps.malls.models import ShoppingCenter
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.users.permissions import IsAdminRole
from apps.workspaces.utils.dashboard_metrics import _empty_metrics, build_extended_metrics
from apps.workspaces.tenant import get_workspace_for_request

_EMPTY = {
    "counts": {
        "centers": 0,
        "spaces": 0,
        "clients": 0,
        "contracts_running": 0,
        "orders": 0,
    },
    "economics": {
        "avg_monthly_price_usd_per_space": None,
        "min_monthly_price_usd_per_space": None,
        "max_monthly_price_usd_per_space": None,
    },
    "orders_by_status": [],
    "spaces_by_availability": [],
    "orders_by_day": [],
    "top_centers_by_spaces": [],
    "metrics": _empty_metrics(),
}


def _date_key(d):
    if d is None:
        return None
    return d.date() if hasattr(d, "date") else d


class AdminDashboardStatsView(APIView):
    """GET: conteos, distribuciones y serie temporal para el dashboard admin."""

    permission_classes = [IsAdminRole]

    def get(self, request):
        ws = get_workspace_for_request(request)
        if ws is None:
            return Response(_EMPTY)

        centers_qs = ShoppingCenter.objects.filter(workspace=ws)
        n_centers = centers_qs.count()

        spaces_qs = AdSpace.objects.filter(shopping_center__workspace=ws)
        n_spaces = spaces_qs.count()

        price_agg = spaces_qs.aggregate(
            avg=Avg("monthly_price_usd"),
            min=Min("monthly_price_usd"),
            max=Max("monthly_price_usd"),
        )

        def _fnum(x):
            if x is None:
                return None
            return float(x)

        avg_monthly_price_usd_per_space = _fnum(price_agg.get("avg"))
        min_monthly_price_usd_per_space = _fnum(price_agg.get("min"))
        max_monthly_price_usd_per_space = _fnum(price_agg.get("max"))

        spaces_by_availability = [
            {"availability": row["availability"], "count": row["c"]}
            for row in spaces_qs.values("availability").annotate(c=Count("id")).order_by("-c")
        ]

        clients_qs = Client.objects.filter(workspace=ws)
        n_clients = clients_qs.count()

        orders_qs = Order.objects.filter(client__workspace=ws)
        n_orders = orders_qs.count()

        orders_by_status = [
            {"status": row["status"], "count": row["c"]}
            for row in orders_qs.values("status").annotate(c=Count("id")).order_by("-c")
        ]

        today = timezone.localdate()
        n_contracts_running = OrderItem.objects.filter(
            order__client__workspace=ws,
            order__status=OrderStatus.ACTIVE,
            start_date__lte=today,
            end_date__gte=today,
        ).count()

        start = today - timedelta(days=29)
        start_dt = timezone.make_aware(datetime.combine(start, time.min))

        orders_by_day_raw = (
            orders_qs.filter(created_at__gte=start_dt)
            .annotate(d=TruncDate("created_at"))
            .values("d")
            .annotate(c=Count("id"))
            .order_by("d")
        )
        by_d = {}
        for row in orders_by_day_raw:
            k = _date_key(row["d"])
            if k is not None:
                by_d[k] = row["c"]

        orders_by_day = []
        for i in range(30):
            d = start + timedelta(days=i)
            orders_by_day.append({"date": d.isoformat(), "count": by_d.get(d, 0)})

        top_centers = list(
            centers_qs.annotate(space_count=Count("ad_spaces"))
            .filter(space_count__gt=0)
            .order_by("-space_count", "name")[:8]
            .values("name", "space_count")
        )
        top_centers_by_spaces = [
            {"name": row["name"], "count": row["space_count"]} for row in top_centers
        ]

        extended = build_extended_metrics(
            ws=ws,
            orders_qs=orders_qs,
            spaces_qs=spaces_qs,
        )

        return Response(
            {
                "counts": {
                    "centers": n_centers,
                    "spaces": n_spaces,
                    "clients": n_clients,
                    "contracts_running": n_contracts_running,
                    "orders": n_orders,
                },
                "economics": {
                    "avg_monthly_price_usd_per_space": avg_monthly_price_usd_per_space,
                    "min_monthly_price_usd_per_space": min_monthly_price_usd_per_space,
                    "max_monthly_price_usd_per_space": max_monthly_price_usd_per_space,
                },
                "orders_by_status": orders_by_status,
                "spaces_by_availability": spaces_by_availability,
                "orders_by_day": orders_by_day,
                "top_centers_by_spaces": top_centers_by_spaces,
                "metrics": extended,
            }
        )
