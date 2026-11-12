from django.utils.dateparse import parse_date
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import ActivityBatch, ActivityBatchItem, ActivitySection, ViewActivityEntry
from apps.audit.serializers import (
    ActivityBatchItemSerializer,
    ActivityBatchSerializer,
    ViewActivityEntrySerializer,
)
from apps.common.pagination import StandardPagination
from apps.core.utils.user_roles import is_client_user
from apps.workspaces.utils.tenant import get_workspace_for_request


class ActivityViewSet(viewsets.ReadOnlyModelViewSet):
    """Historial macro por vista — staff del workspace."""

    serializer_class = ViewActivityEntrySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get_queryset(self):
        if is_client_user(self.request.user):
            return ViewActivityEntry.objects.none()
        ws = get_workspace_for_request(self.request)
        if ws is None:
            return ViewActivityEntry.objects.none()
        qs = ViewActivityEntry.objects.filter(workspace=ws).select_related("actor", "batch")

        section = (self.request.query_params.get("section") or "").strip()
        if section and section in ActivitySection.values:
            qs = qs.filter(section=section)

        action = (self.request.query_params.get("action") or "").strip()
        if action:
            qs = qs.filter(action=action)

        actor = (self.request.query_params.get("actor") or "").strip()
        if actor == "system":
            qs = qs.filter(actor__isnull=True)
        elif actor.isdigit():
            qs = qs.filter(actor_id=int(actor))

        date_from = parse_date(self.request.query_params.get("date_from") or "")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        date_to = parse_date(self.request.query_params.get("date_to") or "")
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        return qs.order_by("-created_at")

    def list(self, request, *args, **kwargs):
        if is_client_user(request.user):
            return Response({"detail": "No autorizado"}, status=403)
        return super().list(request, *args, **kwargs)

    @action(detail=False, methods=["get"], url_path="actors")
    def actors(self, request):
        if is_client_user(request.user):
            return Response({"results": [], "has_system": False})
        ws = get_workspace_for_request(request)
        if ws is None:
            return Response({"results": [], "has_system": False})
        section = (request.query_params.get("section") or "").strip()
        qs = ViewActivityEntry.objects.filter(workspace=ws)
        if section and section in ActivitySection.values:
            qs = qs.filter(section=section)
        has_system = qs.filter(actor__isnull=True).exists()
        from apps.core.utils.display_name import user_display_name

        actor_ids = (
            qs.exclude(actor__isnull=True)
            .values_list("actor_id", flat=True)
            .distinct()[:200]
        )
        User = request.user.__class__
        users = {
            u.id: u
            for u in User.objects.filter(id__in=actor_ids).only(
                "id", "full_name", "first_name", "last_name", "username", "email"
            )
        }
        results = []
        for actor_id in actor_ids:
            user = users.get(actor_id)
            results.append(
                {
                    "id": actor_id,
                    "display": user_display_name(user),
                }
            )
        results.sort(key=lambda row: (row["display"] or "").lower())
        return Response({"results": results, "has_system": has_system})


class ActivityBatchDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        if is_client_user(request.user):
            return Response({"detail": "No autorizado"}, status=403)
        ws = get_workspace_for_request(request)
        if ws is None:
            return Response({"detail": "No autorizado"}, status=403)
        batch = ActivityBatch.objects.filter(pk=pk, workspace=ws).select_related("actor").first()
        if batch is None:
            return Response({"detail": "No encontrado"}, status=404)
        return Response(ActivityBatchSerializer(batch).data)


class ActivityBatchItemsView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get(self, request, pk):
        if is_client_user(request.user):
            return Response({"detail": "No autorizado"}, status=403)
        ws = get_workspace_for_request(request)
        if ws is None:
            return Response({"detail": "No autorizado"}, status=403)
        batch = ActivityBatch.objects.filter(pk=pk, workspace=ws).first()
        if batch is None:
            return Response({"detail": "No encontrado"}, status=404)
        qs = ActivityBatchItem.objects.filter(batch=batch).order_by("id")
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(ActivityBatchItemSerializer(page, many=True).data)
