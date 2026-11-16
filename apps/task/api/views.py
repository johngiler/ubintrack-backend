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
        from apps.audit.services.crud_hooks import SECTION, log_crud
        from apps.notifications.services import notify_task_created

        ws = get_workspace_for_request(self.request)
        if ws is None:
            raise ValidationError({"detail": "Owner no resuelto."})
        obj = serializer.save(workspace=ws)
        log_crud(
            workspace=ws,
            section=SECTION.TASK,
            action="create",
            verb="creó la tarea",
            label=obj.code,
            actor=self.request.user,
            resource_id=obj.pk,
            resource_code=obj.code,
        )
        notify_task_created(task=obj, actor=self.request.user)

    def perform_update(self, serializer):
        from apps.audit.services.crud_hooks import SECTION, log_crud
        from apps.notifications.services import notify_task_status, notify_task_updated

        old_status = getattr(serializer.instance, "status", None)
        obj = serializer.save()
        status_changed = old_status is not None and old_status != obj.status
        if status_changed:
            log_crud(
                workspace=obj.workspace,
                section=SECTION.TASK,
                action="status",
                verb=f"cambió el estado de la tarea a {obj.get_status_display()}",
                label=obj.code,
                actor=self.request.user,
                resource_id=obj.pk,
                resource_code=obj.code,
            )
            notify_task_status(
                task=obj, new_status=obj.status, actor=self.request.user
            )
        else:
            log_crud(
                workspace=obj.workspace,
                section=SECTION.TASK,
                action="update",
                verb="actualizó la tarea",
                label=obj.code,
                actor=self.request.user,
                resource_id=obj.pk,
                resource_code=obj.code,
            )
            notify_task_updated(task=obj, actor=self.request.user)

    def perform_destroy(self, instance):
        from apps.audit.services.crud_hooks import SECTION, log_crud
        from apps.notifications.services import notify_task_deleted

        ws = instance.workspace
        code = instance.code
        pk = instance.pk
        instance.delete()
        log_crud(
            workspace=ws,
            section=SECTION.TASK,
            action="delete",
            verb="eliminó la tarea",
            label=code,
            actor=self.request.user,
            resource_id=pk,
            resource_code=code,
        )
        notify_task_deleted(
            workspace=ws, code=code, resource_id=pk, actor=self.request.user
        )


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
