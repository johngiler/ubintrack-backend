from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.utils.user_roles import is_client_user
from apps.notifications.models import Notification
from apps.notifications.serializers import NotificationSerializer
from apps.notifications.services.deliver import mark_notification_read


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """Campanita — staff del workspace (no clientes)."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if is_client_user(user):
            return Notification.objects.none()
        return Notification.objects.filter(recipient=user)

    def list(self, request, *args, **kwargs):
        if is_client_user(request.user):
            return Response({"detail": "No autorizado"}, status=403)
        return super().list(request, *args, **kwargs)

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        if is_client_user(request.user):
            return Response({"count": 0})
        count = self.get_queryset().filter(read_at__isnull=True).count()
        return Response({"count": count})

    @action(detail=True, methods=["post"], url_path="read")
    def read(self, request, pk=None):
        if is_client_user(request.user):
            return Response({"detail": "No autorizado"}, status=403)
        notification = self.get_object()
        mark_notification_read(notification)
        return Response(NotificationSerializer(notification).data)

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        if is_client_user(request.user):
            return Response({"detail": "No autorizado"}, status=403)
        now = timezone.now()
        updated = (
            self.get_queryset()
            .filter(read_at__isnull=True)
            .update(read_at=now)
        )
        return Response({"updated": updated})
