from rest_framework import viewsets, mixins, filters
from rest_framework.permissions import IsAuthenticated
from utils.permissions_mixins import StrictDjangoModelPermissions, DenyClientUsers
from django_filters.rest_framework import DjangoFilterBackend
from apps.task import models as task_models
from . import serializers


class TaskViewSet(viewsets.ModelViewSet):
    queryset = task_models.Task.objects.all().order_by('-created_at')
    serializer_class = serializers.TaskSerializer
    filter_backends = [DjangoFilterBackend,
                       filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['task_type', 'status', 'priority', 'assigned_to']
    search_fields = ['code', 'observations']
    permission_classes = [IsAuthenticated,
                          DenyClientUsers, StrictDjangoModelPermissions]


class TaskLogViewSet(mixins.CreateModelMixin,
                     mixins.UpdateModelMixin,
                     mixins.DestroyModelMixin,
                     viewsets.GenericViewSet):
    queryset = task_models.TaskLog.objects.all().order_by('-created_at')
    serializer_class = serializers.TaskLogSerializer
    permission_classes = [IsAuthenticated,
                          DenyClientUsers, StrictDjangoModelPermissions]


class UserTaskTrackingViewSet(viewsets.ModelViewSet):
    queryset = task_models.UserTaskTracking.objects.all().order_by('-created_at')
    serializer_class = serializers.UserTaskTrackingSerializer
    permission_classes = [IsAuthenticated,
                          DenyClientUsers, StrictDjangoModelPermissions]
