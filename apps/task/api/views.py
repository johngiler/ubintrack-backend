from rest_framework import viewsets, mixins, filters
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from apps.common.utils.permissions_mixins import (
    StrictDjangoModelPermissions,
    DenyClientUsers,
    WorkspaceScopeMixin,
)
from django_filters.rest_framework import DjangoFilterBackend
from apps.workspaces.utils.tenant import get_workspace_for_request
from apps.task import models as task_models
from . import serializers


class TaskViewSet(WorkspaceScopeMixin, viewsets.ModelViewSet):
    queryset = task_models.Task.objects.all().order_by('-created_at')
    serializer_class = serializers.TaskSerializer
    filter_backends = [DjangoFilterBackend,
                       filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['task_type', 'status', 'priority', 'assigned_to']
    search_fields = ['code', 'observations']
    permission_classes = [IsAuthenticated,
                          DenyClientUsers, StrictDjangoModelPermissions]
    workspace_field = 'workspace'

    def get_queryset(self):
        return self.scope_to_workspace(super().get_queryset())

    def perform_create(self, serializer):
        ws = get_workspace_for_request(self.request)
        if ws is None:
            raise ValidationError({'detail': 'Owner no resuelto.'})
        serializer.save(workspace=ws)


class TaskLogViewSet(WorkspaceScopeMixin,
                     mixins.ListModelMixin,
                     mixins.RetrieveModelMixin,
                     viewsets.GenericViewSet):
    queryset = task_models.TaskLog.objects.select_related('task').order_by('-created_at')
    serializer_class = serializers.TaskLogSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['task']
    ordering_fields = ['created_at']
    ordering = ['-created_at']
    permission_classes = [IsAuthenticated,
                          DenyClientUsers, StrictDjangoModelPermissions]
    workspace_field = 'task__workspace'

    def get_queryset(self):
        return self.scope_to_workspace(super().get_queryset())


class UserTaskTrackingViewSet(WorkspaceScopeMixin, viewsets.ModelViewSet):
    queryset = task_models.UserTaskTracking.objects.select_related(
        'user', 'task',
    ).order_by('-created_at')
    serializer_class = serializers.UserTaskTrackingSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['user', 'task']
    ordering_fields = ['timer_start', 'created_at']
    ordering = ['-created_at']
    permission_classes = [IsAuthenticated,
                          DenyClientUsers, StrictDjangoModelPermissions]
    workspace_field = 'user__workspace'

    def get_queryset(self):
        return self.scope_to_workspace(super().get_queryset())
