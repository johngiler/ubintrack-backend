import logging

from rest_framework import viewsets, filters, status
from rest_framework.exceptions import ValidationError
from apps.core.utils.user_roles import is_client_user
from rest_framework.permissions import IsAuthenticated
from apps.common.utils.permissions_mixins import DenyClientUsers, StrictDjangoModelPermissions, WorkspaceScopeMixin
from apps.workspaces.utils.tenant import get_workspace_for_request
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.common.pagination import MediumPagination
from django_filters.rest_framework import DjangoFilterBackend
import django_filters
from django.core.files import File
from django.db.models import Count, Prefetch, Q

from apps.storage import models as storage_models
from apps.storage.generation import iter_slots
from apps.storage.tasks import generate_cava_positions

logger = logging.getLogger(__name__)
from apps.inventory import models as inventory_models
from apps.common.utils.zebra import print_to_zebra

from . import serializers


class PositionNomenclatureView(APIView):
    permission_classes = [IsAuthenticated]

    def _workspace(self, request):
        ws = get_workspace_for_request(request)
        if ws is None:
            raise ValidationError({"detail": "No se pudo completar la operación."})
        return ws

    def get(self, request):
        if is_client_user(request.user):
            return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)
        ws = self._workspace(request)
        obj, _created = storage_models.PositionNomenclature.objects.get_or_create(workspace=ws)
        return Response(serializers.PositionNomenclatureSerializer(obj).data)

    def patch(self, request):
        if is_client_user(request.user):
            return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)
        ws = self._workspace(request)
        obj, _created = storage_models.PositionNomenclature.objects.get_or_create(workspace=ws)
        serializer = serializers.PositionNomenclatureSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


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
    queryset = (
        storage_models.Cava.objects.select_related("storage")
        .prefetch_related("blocks")
        .annotate(position_count=Count("positions"))
        .order_by("-created_at")
    )
    serializer_class = serializers.CavaSerializer
    pagination_class = MediumPagination
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]
    workspace_field = "storage__workspace"
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "storage__name"]

    def get_queryset(self):
        return self.scope_to_workspace(super().get_queryset())

    @action(detail=True, methods=["post"])
    def generate(self, request, pk=None):
        if is_client_user(request.user):
            return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)

        cava = self.get_object()
        active = storage_models.CavaGenerationJob.Status
        if cava.generation_jobs.filter(status__in=(active.PENDING, active.RUNNING)).exists():
            return Response(
                {"detail": "En proceso. Puedes continuar mientras se crean."},
                status=status.HTTP_409_CONFLICT,
            )
        slot_count = sum(1 for _ in iter_slots(cava))
        if slot_count < 1:
            return Response(
                {"detail": "Agregue al menos un rack."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        workspace = cava.storage.workspace
        job = storage_models.CavaGenerationJob.objects.create(
            cava=cava,
            workspace=workspace,
            expected_count=slot_count,
        )
        if slot_count == 1:
            return self._generate_now(job)

        try:
            generate_cava_positions.delay(job.id)
        except Exception:
            logger.exception("No se pudo encolar la generación de la cava %s", cava.id)
            job.status = active.FAILED
            job.save(update_fields=["status", "updated_at"])
            return Response(
                {"detail": "No se pudo iniciar la generación."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            serializers.CavaGenerationJobSerializer(job).data,
            status=status.HTTP_202_ACCEPTED,
        )

    def _generate_now(self, job):
        from apps.storage.generation import generate_positions_for_cava

        active = storage_models.CavaGenerationJob.Status
        job.status = active.RUNNING
        job.save(update_fields=["status", "updated_at"])
        try:
            result = generate_positions_for_cava(job.cava)
        except Exception:
            logger.exception("No se pudo crear la posición de la cava %s", job.cava_id)
            job.status = active.FAILED
            job.save(update_fields=["status", "updated_at"])
            return Response(
                {"detail": "No se pudieron crear las posiciones."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        job.status = active.SUCCEEDED
        job.expected_count = result["expected"]
        job.created_count = result["created"]
        job.skipped_count = result["skipped"]
        job.save(
            update_fields=[
                "status",
                "expected_count",
                "created_count",
                "skipped_count",
                "updated_at",
            ]
        )
        return Response(serializers.CavaGenerationJobSerializer(job).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def occupancy(self, request, pk=None):
        cava = self.get_object()
        rows = cava.positions.order_by("hall", "rack", "place", "level").values(
            "hall", "rack", "level", "place", "status", "is_active", "is_picking", "is_push"
        )
        return Response(list(rows))

    @action(detail=True, methods=["post"], url_path="set_hilera_push")
    def set_hilera_push(self, request, pk=None):
        """Activa/desactiva is_push en todas las posiciones de una hilera (racks)."""
        if is_client_user(request.user):
            return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)

        from apps.storage.utils.conduits import MIN_PUSH_DEPTH

        cava = self.get_object()
        hall = str(request.data.get("hall") or "").strip()
        racks_raw = request.data.get("racks") or []
        if not isinstance(racks_raw, list):
            return Response(
                {"detail": "No se pudo completar la operación."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        racks = [str(item).strip() for item in racks_raw if str(item).strip()]
        if not racks:
            return Response(
                {"detail": "No se pudo completar la operación."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        is_push = bool(request.data.get("is_push"))
        qs = cava.positions.filter(hall=hall, rack__in=racks)
        if not qs.exists():
            return Response(
                {"detail": "No se pudo completar la operación."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if is_push:
            shallow = (
                qs.values("rack", "level")
                .annotate(depth=Count("place", distinct=True))
                .filter(depth__lt=MIN_PUSH_DEPTH)
            )
            if shallow.exists():
                return Response(
                    {"detail": f"La profundidad del conducto debe ser de al menos {MIN_PUSH_DEPTH}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        updated = qs.update(is_push=is_push)
        return Response({"updated": updated, "is_push": is_push})

    @action(detail=True, methods=["get"])
    def generation(self, request, pk=None):
        cava = self.get_object()
        job = cava.generation_jobs.order_by("-id").first()
        if job is None:
            return Response({"status": "idle", "detail": ""})
        return Response(serializers.CavaGenerationJobSerializer(job).data)


class ExactOptionalFilter(django_filters.CharFilter):
    """Filtra solo si el parámetro viene en la petición. La cadena vacía es un pasillo en blanco."""

    def filter(self, qs, value):
        data = getattr(self.parent, "data", None)
        if data is None or self.field_name not in data:
            return qs
        return qs.filter(**{self.field_name: str(value or "").strip()})


class PositionFilter(django_filters.FilterSet):
    hall = ExactOptionalFilter(field_name="hall")
    rack = ExactOptionalFilter(field_name="rack")
    place = ExactOptionalFilter(field_name="place")
    place_in = django_filters.BaseInFilter(field_name="place", lookup_expr="in")

    class Meta:
        model = storage_models.Position
        fields = [
            "status",
            "cava",
            "is_active",
            "is_picking",
            "is_push",
            "hall",
            "rack",
            "place",
        ]


class PositionViewSet(WorkspaceScopeMixin, viewsets.ModelViewSet):
    queryset = storage_models.Position.objects.all().order_by('code')
    serializer_class = serializers.PositionSerializer
    pagination_class = MediumPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_class = PositionFilter
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
