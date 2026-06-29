import logging

from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.user_roles import can_manage_as_administrator
from apps.workspaces.serializers import (
    WorkspaceMeReadSerializer,
    WorkspaceMeUpdateSerializer,
    WorkspacePublicSerializer,
)
from apps.workspaces.tenant import get_workspace_for_request, user_can_access_workspace

logger = logging.getLogger(__name__)


class IsWorkspaceAdministrator(BasePermission):
    def has_permission(self, request, view):
        return can_manage_as_administrator(request.user)


class WorkspaceCurrentView(APIView):
    """
    Datos públicos del owner (Workspace) según Host / Origin de la petición.
    El front usa esto para marca, título y colores.
    """

    permission_classes = []

    def get(self, request):
        ws = get_workspace_for_request(request)
        if ws is None:
            return Response({"detail": "No hay workspace para este contexto."}, status=404)
        ser = WorkspacePublicSerializer(ws, context={"request": request})
        return Response(ser.data)


class WorkspaceMeView(APIView):
    """
    GET/PATCH configuración del owner actual.
    Solo administradores del workspace. El slug es solo lectura.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceAdministrator]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _get_workspace(self, request):
        ws = get_workspace_for_request(request)
        if ws is None:
            raise NotFound({"detail": "No hay workspace para este contexto."})
        if not user_can_access_workspace(request.user, ws):
            raise PermissionDenied({"detail": "No autorizado para este owner."})
        return ws

    def get(self, request):
        ws = self._get_workspace(request)
        return Response(WorkspaceMeReadSerializer(ws, context={"request": request}).data)

    def patch(self, request):
        ws = self._get_workspace(request)
        serializer = WorkspaceMeUpdateSerializer(
            ws,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(WorkspaceMeReadSerializer(ws, context={"request": request}).data)
