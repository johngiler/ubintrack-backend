"""
Actividad reciente del panel admin: pedidos (transiciones), altas de clientes y usuarios.
"""

from __future__ import annotations

from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.clients.models import Client
from apps.orders.models import OrderStatus, OrderStatusEvent
from apps.users.models import UserProfile
from apps.users.permissions import IsAdminRole
from apps.workspaces.tenant import get_workspace_for_request

User = get_user_model()


def _order_status_label(code: str) -> str:
    if not code:
        return "Inicio"
    try:
        return str(OrderStatus(code).label)
    except ValueError:
        return code


def _actor_label(user) -> str:
    if user is None:
        return "Sistema"
    fn = (user.get_full_name() or "").strip()
    if fn:
        return fn
    return (user.username or "").strip() or "Usuario"


def _marketplace_users_base_qs(workspace):
    if workspace is None:
        return User.objects.none()
    return (
        User.objects.filter(is_staff=False, is_superuser=False)
        .filter(Q(profile__workspace=workspace) | Q(profile__client__workspace=workspace))
        .distinct()
    )


def _user_role_label(user) -> str:
    try:
        p = user.profile
    except UserProfile.DoesNotExist:
        return ""
    if p.role == UserProfile.Role.ADMIN:
        return "Administrador del marketplace"
    if p.role == UserProfile.Role.CLIENT:
        return "Cliente marketplace"
    return ""


class AdminDashboardActivityView(APIView):
    """
    GET: lista unificada de hitos recientes (workspace actual).
    Query: limit (default 35, max 80).
    """

    permission_classes = [IsAdminRole]

    def get(self, request):
        ws = get_workspace_for_request(request)
        try:
            limit = int(request.query_params.get("limit", 35))
        except (TypeError, ValueError):
            limit = 35
        limit = max(1, min(limit, 80))

        if ws is None:
            return Response({"activities": []})

        events = list(
            OrderStatusEvent.objects.filter(order__client__workspace=ws)
            .select_related("order", "order__client", "actor")
            .order_by("-created_at", "-id")[:45]
        )

        clients = list(Client.objects.filter(workspace=ws).order_by("-created_at")[:12])

        users = list(
            _marketplace_users_base_qs(ws)
            .select_related("profile", "profile__client")
            .order_by("-date_joined", "-id")[:12]
        )

        rows: list[dict] = []

        for ev in events:
            order = ev.order
            ref = (order.code or "").strip() or f"Pedido #{order.pk}"
            client = order.client
            company = (client.company_name or "").strip() if client else ""
            from_l = _order_status_label(ev.from_status)
            to_l = _order_status_label(ev.to_status)
            q = ref.removeprefix("#").strip() or str(order.pk)
            rows.append(
                {
                    "_ts": ev.created_at,
                    "id": f"order-event-{ev.pk}",
                    "kind": "order_status_changed",
                    "at": ev.created_at,
                    "title": ref,
                    "primary_line": f"Flujo del pedido: {from_l} → {to_l}",
                    "secondary_line": " · ".join(
                        p
                        for p in (
                            f"Hecho por: {_actor_label(ev.actor)}",
                            f"Empresa: {company}" if company else "",
                        )
                        if p
                    ),
                    "tertiary_line": (ev.note or "").strip() or None,
                    "href": f"/dashboard/pedidos?q={quote(q)}",
                }
            )

        for c in clients:
            q = (c.company_name or c.rif or c.email or str(c.pk)).strip()
            rif = (c.rif or "").strip()
            rows.append(
                {
                    "_ts": c.created_at,
                    "id": f"client-{c.pk}",
                    "kind": "client_created",
                    "at": c.created_at,
                    "title": "Empresa registrada en el panel",
                    "primary_line": (c.company_name or "Empresa sin nombre comercial").strip(),
                    "secondary_line": f"RIF: {rif}" if rif else None,
                    "tertiary_line": (c.email or "").strip() or None,
                    "href": f"/dashboard/clientes?q={quote(q)}",
                }
            )

        for u in users:
            uname = (u.username or "").strip() or f"#{u.pk}"
            email = (u.email or "").strip()
            role_l = _user_role_label(u)
            q = email or uname
            rows.append(
                {
                    "_ts": u.date_joined,
                    "id": f"user-{u.pk}",
                    "kind": "user_created",
                    "at": u.date_joined,
                    "title": "Usuario del marketplace dado de alta",
                    "primary_line": uname,
                    "secondary_line": " · ".join(
                        p for p in (email, role_l) if p
                    ),
                    "tertiary_line": None,
                    "href": f"/dashboard/usuarios?q={quote(q)}",
                }
            )

        rows.sort(key=lambda r: r["_ts"], reverse=True)

        out = []
        for r in rows[:limit]:
            r.pop("_ts", None)
            at_val = r["at"]
            if timezone.is_naive(at_val):
                at_val = timezone.make_aware(at_val, timezone.get_current_timezone())
            r["at"] = at_val.isoformat()
            out.append(r)

        return Response({"activities": out})
