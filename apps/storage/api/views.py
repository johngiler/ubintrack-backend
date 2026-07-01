from rest_framework import viewsets, filters, status
from rest_framework.exceptions import ValidationError
from apps.core.utils.user_roles import get_user_client, is_client_user, can_manage_as_administrator
from rest_framework.permissions import IsAuthenticated
from apps.common.utils.permissions_mixins import DenyClientUsers, StrictDjangoModelPermissions, WorkspaceScopeMixin
from apps.workspaces.utils.tenant import get_workspace_for_request
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.common.pagination import MediumPagination
from django_filters.rest_framework import DjangoFilterBackend
from django.core.files import File
from django.db.models import Prefetch, Q

from apps.storage import models as storage_models
from apps.inventory import models as inventory_models
from apps.common.utils.zebra import print_to_zebra

from . import serializers


class StorageViewSet(WorkspaceScopeMixin, viewsets.ModelViewSet):
    queryset = storage_models.Storage.objects.all().order_by("-created_at")
    serializer_class = serializers.StorageSerializer
    pagination_class = MediumPagination
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ["name"]

    def get_queryset(self):
        return self.scope_to_workspace(super().get_queryset())

    def perform_create(self, serializer):
        ws = get_workspace_for_request(self.request)
        if ws is None:
            raise ValidationError({"detail": "Workspace no resuelto."})
        serializer.save(workspace=ws)


class CavaViewSet(WorkspaceScopeMixin, viewsets.ModelViewSet):
    queryset = storage_models.Cava.objects.select_related("storage").all().order_by("-created_at")
    serializer_class = serializers.CavaSerializer
    pagination_class = MediumPagination
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]
    workspace_field = "storage__workspace"
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "storage__name"]

    def get_queryset(self):
        return self.scope_to_workspace(super().get_queryset())


class PositionViewSet(WorkspaceScopeMixin, viewsets.ModelViewSet):
    queryset = storage_models.Position.objects.all().order_by('code')
    serializer_class = serializers.PositionSerializer
    pagination_class = MediumPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['status', 'hall', 'cava', 'is_active']
    search_fields = ['code',]
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]
    workspace_field = "cava__storage__workspace"

    def get_queryset(self):
        qs = (
            storage_models.Position.objects
            .select_related("cava", "cava__storage")
            .prefetch_related(
                Prefetch(
                    "bins",
                    queryset=inventory_models.Bin.objects.select_related(
                        "client", "entry"
                    ).order_by("-created_at"),
                )
            )
            .order_by("cava__name", "hall", "rack", "level", "place")
        )
        return self.scope_to_workspace(qs)

    def filter_queryset(self, queryset):
        # Búsqueda personalizada por código de posición, código de bin o cliente
        search = self.request.query_params.get('search', '')
        if search:
            queryset = queryset.filter(
                Q(code__icontains=search) |
                Q(bins__code__icontains=search) |
                Q(bins__client__name__icontains=search)
            ).distinct()
        
        # Aplicar filtros estándar (excepto search que ya lo manejamos)
        for backend in self.filter_backends:
            if backend != filters.SearchFilter:
                queryset = backend().filter_queryset(self.request, queryset, self)
        
        return queryset.order_by('cava__name', 'hall', 'rack', 'level', 'place')

    @action(detail=True, methods=['post'])
    def print(self, request, pk=None):
        """
        Endpoint para imprimir el ZPL de una posición
        """
        # Restringir impresión para clientes
        if is_client_user(request.user):
            return Response({'detail': 'No autorizado.'}, status=status.HTTP_403_FORBIDDEN)

        position = self.get_object()

        # Verificar si tiene código ZPL
        if not position.zpl_code:
            return Response({
                'success': False,
                'message': f'La posición {position.code} no tiene código ZPL generado'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Imprimir el ZPL
        success = print_to_zebra(position.zpl_code)

        if success:
            return Response({
                'success': True,
                'message': f'Etiqueta de la posición {position.code} enviada a impresión'
            })
        else:
            return Response({
                'success': False,
                'message': 'Error al enviar a la impresora. Verifique la conexión.'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def regenerate_tag(self, request, pk=None):
        """
        Endpoint para regenerar el tag y ZPL de una posición
        """
        # Restringir regeneración para clientes
        if is_client_user(request.user):
            return Response({'detail': 'No autorizado.'}, status=status.HTTP_403_FORBIDDEN)

        position = self.get_object()

        # Regenerar el QR y el ZPL
        qr_buffer = storage_models.services.generate_qr_with_label(
            position.code, position.code, position.level)
        filename = f"qrcode-{position.code}.webp"
        position.qr_code_image.save(
            filename, File(qr_buffer), save=False)

        # Regenerar código ZPL
        position.zpl_code = storage_models.services.generate_position_tag_zpl(
            position)
        position.save(update_fields=['qr_code_image', 'zpl_code'])

        return Response({
            'success': True,
            'message': f'Tag y ZPL de la posición {position.code} regenerados correctamente'
        })
