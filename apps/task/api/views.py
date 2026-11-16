from django.db.models import Count, Q
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, mixins, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.common.utils.permissions_mixins import (
    DenyClientUsers,
    StrictDjangoModelPermissions,
    WorkspaceScopeMixin,
)
from apps.core.choices import TaskLineStatus
from apps.task import models as task_models
from apps.workspaces.utils.tenant import get_workspace_for_request

from . import serializers


class TaskViewSet(WorkspaceScopeMixin, viewsets.ModelViewSet):
    queryset = task_models.Task.objects.all().order_by("-created_at")
    serializer_class = serializers.TaskSerializer
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = [
        "task_type",
        "status",
        "priority",
        "assigned_to",
        "client",
        "cavas",
        "products",
    ]
    search_fields = ["code", "title", "observations"]
    ordering_fields = ["created_at", "started_at", "completed_at", "priority"]
    ordering = ["-created_at"]
    permission_classes = [
        IsAuthenticated,
        DenyClientUsers,
        StrictDjangoModelPermissions,
    ]
    workspace_field = "workspace"

    def get_serializer_class(self):
        if self.action == "list":
            return serializers.TaskListSerializer
        return serializers.TaskSerializer

    def get_queryset(self):
        qs = (
            self.scope_to_workspace(super().get_queryset())
            .select_related("client")
            .prefetch_related("assigned_to", "cavas", "products")
            .annotate(
                lines_count=Count("lines", distinct=True),
                lines_done=Count(
                    "lines",
                    filter=~Q(lines__line_status=TaskLineStatus.PENDING),
                    distinct=True,
                ),
            )
        )
        if self.action == "retrieve":
            qs = qs.prefetch_related("task_logs")
        return qs

    def perform_create(self, serializer):
        ws = get_workspace_for_request(self.request)
        if ws is None:
            raise ValidationError({"detail": "Owner no resuelto."})
        serializer.save(workspace=ws)


class TaskLogViewSet(
    WorkspaceScopeMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = task_models.TaskLog.objects.select_related("task").order_by(
        "-created_at"
    )
    serializer_class = serializers.TaskLogSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["task"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]
    permission_classes = [
        IsAuthenticated,
        DenyClientUsers,
        StrictDjangoModelPermissions,
    ]
    workspace_field = "task__workspace"

    def get_queryset(self):
        return self.scope_to_workspace(super().get_queryset())


class TaskLineViewSet(
    WorkspaceScopeMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = task_models.TaskLine.objects.select_related(
        "task", "position", "bin"
    ).order_by("sort_order", "id")
    serializer_class = serializers.TaskLineSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["task", "line_status", "position"]
    ordering_fields = ["sort_order", "created_at"]
    ordering = ["sort_order"]
    permission_classes = [
        IsAuthenticated,
        DenyClientUsers,
        StrictDjangoModelPermissions,
    ]
    workspace_field = "task__workspace"

    def get_queryset(self):
        return self.scope_to_workspace(super().get_queryset())
