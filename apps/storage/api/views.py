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
        .annotate(
            position_count=Count("positions"),
            active_position_count=Count(
                "positions",
                filter=Q(positions__is_active=True),
            ),
        )
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

    @action(detail=False, methods=["get"])
    def totals(self, request):
        """Suma de posiciones (activas / total) de todas las cavas del owner."""
        cava_ids = self.filter_queryset(self.get_queryset()).values_list("id", flat=True)
        agg = storage_models.Position.objects.filter(cava_id__in=cava_ids).aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(is_active=True)),
        )
        return Response(
            {
                "total": agg["total"] or 0,
                "active": agg["active"] or 0,
            }
        )

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
            # Solo activas; push apaga picking. Inactivas sin push/picking.
            qs.filter(is_active=False).update(is_push=False, is_picking=False)
            updated = qs.filter(is_active=True).update(is_push=True, is_picking=False)
        else:
            updated = qs.update(is_push=False)
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

    BULK_MAX_IDS = 2000

    def _parse_bulk_ids(self, request):
        raw = request.data.get("ids") or []
        if not isinstance(raw, list) or not raw:
            return None, Response(
                {"detail": "Seleccione al menos una posición."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            ids = [int(value) for value in raw]
        except (TypeError, ValueError):
            return None, Response(
                {"detail": "No se pudo completar la operación."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(ids) > self.BULK_MAX_IDS:
            return None, Response(
                {"detail": f"Máximo {self.BULK_MAX_IDS} posiciones por operación."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return ids, None

    @action(detail=False, methods=["get"])
    def halls(self, request):
        """Tokens de sección (P1, T1, …) presentes en posiciones, opcionalmente por cava."""
        qs = self.get_queryset()
        cava_id = request.query_params.get("cava")
        if cava_id:
            qs = qs.filter(cava_id=cava_id)
        tokens = (
            qs.exclude(hall="")
            .values_list("hall", flat=True)
            .distinct()
            .order_by("hall")
        )
        return Response(
            [{"value": token, "label": token} for token in tokens if str(token).strip()]
        )

    @action(detail=False, methods=["get"])
    def ids(self, request):
        """IDs del listado filtrado (para seleccionar todas tras filtrar)."""
        qs = self.filter_queryset(self.get_queryset())
        id_list = list(qs.values_list("id", flat=True)[: self.BULK_MAX_IDS + 1])
        if len(id_list) > self.BULK_MAX_IDS:
            return Response(
                {"detail": f"Máximo {self.BULK_MAX_IDS} posiciones por operación."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"ids": id_list, "count": len(id_list)})

    @action(detail=False, methods=["delete"], url_path="bulk-delete")
    def bulk_delete(self, request):
        """Elimina en masa posiciones libres del scope del usuario."""
        if is_client_user(request.user):
            return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)

        ids, error = self._parse_bulk_ids(request)
        if error is not None:
            return error

        qs = self.scope_to_workspace(
            storage_models.Position.objects.filter(id__in=ids)
        )
        found_ids = set(qs.values_list("id", flat=True))
        missing = len(ids) - len(found_ids)

        blocked_count = qs.exclude(status="libre").count()
        deleted_count, _ = qs.filter(status="libre").delete()

        parts = [f"Se eliminaron {deleted_count} posiciones."]
        if blocked_count:
            parts.append(f"{blocked_count} no estaban libres.")
        if missing:
            parts.append(f"{missing} no encontradas.")
        return Response(
            {
                "success": True,
                "deleted": deleted_count,
                "blocked": blocked_count,
                "message": " ".join(parts),
            }
        )

    @staticmethod
    def _optional_bool(data, key):
        if key not in data:
            return None
        value = data.get(key)
        if isinstance(value, bool):
            return value
        if value in (0, 1, "0", "1", "true", "false", "True", "False"):
            return str(value).lower() in ("1", "true")
        return None

    @action(detail=False, methods=["post"], url_path="bulk-update-flags")
    def bulk_update_flags(self, request):
        """
        Cambia is_active / is_picking / is_push en masa.
        Reglas: inactiva → sin push/picking; push ↔ picking excluyentes;
        push aplica al conducto completo (profundidad ≥ MIN_PUSH_DEPTH).
        """
        if is_client_user(request.user):
            return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)

        from django.core.exceptions import ValidationError as DjangoValidationError

        from apps.storage.utils.conduits import set_conduit_is_push

        ids, error = self._parse_bulk_ids(request)
        if error is not None:
            return error

        is_active = self._optional_bool(request.data, "is_active")
        is_picking = self._optional_bool(request.data, "is_picking")
        is_push = self._optional_bool(request.data, "is_push")
        if is_active is None and is_picking is None and is_push is None:
            return Response(
                {"detail": "Indique al menos un cambio."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if is_push is True and is_picking is True:
            return Response(
                {"detail": "Push y picking no pueden estar activos a la vez."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if is_active is False and (is_push is True or is_picking is True):
            return Response(
                {"detail": "Una posición inactiva no puede ser push ni picking."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = self.scope_to_workspace(
            storage_models.Position.objects.filter(id__in=ids)
        )
        if not qs.exists():
            return Response(
                {"detail": "No se encontraron posiciones."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        touched = 0
        skipped_inactive = 0
        conduits_ok = 0

        if is_active is False:
            touched = qs.update(is_active=False, is_picking=False, is_push=False)
            return Response(
                {
                    "success": True,
                    "updated": touched,
                    "message": f"Se desactivaron {touched} posiciones.",
                }
            )

        if is_active is True:
            touched += qs.filter(is_active=False).update(is_active=True)

        # Push (conducto) antes que picking si se pide apagar push / encender push.
        if is_push is False:
            seen = set()
            for position in qs.iterator():
                key = (position.cava_id, position.hall, position.rack, position.level)
                if key in seen:
                    continue
                seen.add(key)
                conduits_ok += set_conduit_is_push(position, False)
            touched = max(touched, conduits_ok)

        if is_push is True:
            seen = set()
            try:
                for position in qs.iterator():
                    key = (position.cava_id, position.hall, position.rack, position.level)
                    if key in seen:
                        continue
                    seen.add(key)
                    # Representante activo del conducto (set_conduit exige activo).
                    sample = (
                        qs.filter(
                            cava_id=position.cava_id,
                            hall=position.hall,
                            rack=position.rack,
                            level=position.level,
                            is_active=True,
                        ).first()
                        or position
                    )
                    if not sample.is_active:
                        skipped_inactive += 1
                        continue
                    conduits_ok += set_conduit_is_push(sample, True)
            except DjangoValidationError as exc:
                msg = (
                    "; ".join(exc.messages)
                    if hasattr(exc, "messages")
                    else str(exc)
                )
                return Response({"detail": msg}, status=status.HTTP_400_BAD_REQUEST)
            touched = max(touched, conduits_ok)

        if is_picking is True:
            # Picking apaga push en el conducto de cada seleccionada activa.
            seen = set()
            for position in qs.filter(is_active=True).iterator():
                key = (position.cava_id, position.hall, position.rack, position.level)
                if key in seen:
                    continue
                seen.add(key)
                set_conduit_is_push(position, False)
            picking_updated = qs.filter(is_active=True).update(is_picking=True)
            skipped_inactive += qs.filter(is_active=False).count()
            touched = max(touched, picking_updated)

        if is_picking is False:
            picking_off = qs.update(is_picking=False)
            touched = max(touched, picking_off)

        parts = [f"Se actualizaron {touched} posiciones."]
        if skipped_inactive:
            parts.append(f"{skipped_inactive} inactivas omitidas.")
        return Response(
            {
                "success": True,
                "updated": touched,
                "skipped_inactive": skipped_inactive,
                "message": " ".join(parts),
            }
        )

    @action(detail=False, methods=["post"], url_path="bulk-print")
    def bulk_print(self, request):
        """Imprime etiquetas ZPL de varias posiciones."""
        if is_client_user(request.user):
            return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)

        ids, error = self._parse_bulk_ids(request)
        if error is not None:
            return error

        positions = list(
            self.scope_to_workspace(
                storage_models.Position.objects.filter(id__in=ids)
            )
            .only("id", "code", "zpl_code")
            .order_by("cava__name", "hall", "rack", "level", "place")
        )
        if not positions:
            return Response(
                {"detail": "No se encontraron posiciones."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        skipped = [p.code for p in positions if not p.zpl_code]
        zpl_jobs = [p.zpl_code for p in positions if p.zpl_code]
        if not zpl_jobs:
            return Response(
                {
                    "success": False,
                    "printed": 0,
                    "skipped": skipped,
                    "message": "Ninguna posición tiene etiqueta para imprimir.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Un envío con todos los ZPL (varios ^XA…^XZ) evita N conexiones.
        if not print_to_zebra("\n".join(zpl_jobs)):
            return Response(
                {
                    "success": False,
                    "printed": 0,
                    "skipped": skipped,
                    "message": "Error al enviar a la impresora. Verifique la conexión.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        printed = len(zpl_jobs)
        parts = [f"Se enviaron {printed} etiquetas."]
        if skipped:
            parts.append(f"{len(skipped)} sin ZPL.")
        return Response(
            {
                "success": True,
                "printed": printed,
                "skipped": skipped,
                "message": " ".join(parts),
            }
        )

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
