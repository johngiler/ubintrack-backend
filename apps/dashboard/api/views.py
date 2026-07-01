from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.utils.user_roles import can_manage_as_administrator, is_client_user
from apps.dashboard import services as dashboard_services
from apps.workspaces.utils.tenant import get_workspace_for_request, is_platform_staff


class IsOwnerAdministrator(BasePermission):
    """Solo administradores del owner (no empleados ni clientes)."""

    message = 'No autorizado'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if is_client_user(user):
            return False
        if not can_manage_as_administrator(user):
            return False
        workspace = get_workspace_for_request(request)
        if workspace is None:
            return False
        if is_platform_staff(user):
            return True
        return getattr(user, 'workspace_id', None) == workspace.id


class DashboardStatsView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdministrator]

    def get(self, request):
        workspace = get_workspace_for_request(request)
        data = {
            'stats': dashboard_services.build_dashboard_stats(workspace),
            'charts': {
                'monthly': dashboard_services.get_monthly_series(workspace),
                'daily': dashboard_services.get_daily_last7(workspace),
            },
            'timestamp': timezone.now(),
        }
        return Response(data, status=status.HTTP_200_OK)


class WarehouseStructureView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdministrator]

    def get(self, request):
        workspace = get_workspace_for_request(request)
        structure = dashboard_services.build_warehouse_structure(workspace)
        if structure is None:
            return Response({
                'success': False,
                'error': 'No se encontró almacén activo para este owner',
            }, status=status.HTTP_404_NOT_FOUND)

        return Response({
            'success': True,
            'data': structure,
        }, status=status.HTTP_200_OK)


class ClientStatsView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdministrator]

    def get(self, request):
        workspace = get_workspace_for_request(request)
        data = dashboard_services.build_client_stats(workspace)
        return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)
