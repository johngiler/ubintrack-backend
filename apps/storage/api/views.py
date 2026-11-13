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
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Prefetch, Q, Value, When
from django.db.models.functions import Cast

from apps.storage import models as storage_models
from apps.storage.generation import iter_slots
from apps.storage.tasks import generate_cava_positions
from apps.workspaces.models import Workspace

logger = logging.getLogger(__name__)
from apps.inventory import models as inventory_models
from apps.common.utils.zebra import (
    USER_PRINT_FAILED,
    USER_PRINTER_NOT_CONFIGURED,
    printer_is_configured,
    print_to_zebra,
)

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
        .order_by("name", "id")
    )
    serializer_class = serializers.CavaSerializer
    pagination_class = MediumPagination
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]
    workspace_field = "storage__workspace"
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "storage__name"]

    def get_queryset(self):
        qs = self.scope_to_workspace(super().get_queryset())
        # Mismo nombre (p. ej. «Cámara») → ordenar por código numérico (2 antes que 9).
        return qs.annotate(
            _code_num=Case(
                When(code__regex=r"^\d+$", then=Cast("code", IntegerField())),
                default=Value(None),
                output_field=IntegerField(),
            )
        ).order_by("name", "_code_num", "code", "id")

    def perform_create(self, serializer):
        from apps.audit.services.crud_hooks import SECTION, log_crud
        from apps.notifications.services import notify_cava_created

        obj = serializer.save()
        ws = getattr(getattr(obj, "storage", None), "workspace", None)
        label = obj.name or obj.code or str(obj.pk)
        log_crud(
            workspace=ws,
            section=SECTION.CAVA,
            action="create",
            verb="creó la cava",
            label=label,
            actor=self.request.user,
            resource_id=obj.pk,
            resource_code=label,
        )
        notify_cava_created(cava=obj, actor=self.request.user)

    def perform_update(self, serializer):
        from apps.audit.services.crud_hooks import SECTION, log_crud
        from apps.notifications.services import notify_cava_updated

        obj = serializer.save()
        ws = getattr(getattr(obj, "storage", None), "workspace", None)
        label = obj.name or obj.code or str(obj.pk)
        log_crud(
            workspace=ws,
            section=SECTION.CAVA,
            action="update",
            verb="actualizó la cava",
            label=label,
            actor=self.request.user,
            resource_id=obj.pk,
            resource_code=label,
        )
        notify_cava_updated(cava=obj, actor=self.request.user)

    def perform_destroy(self, instance):
        from apps.audit.services.crud_hooks import SECTION, log_crud
        from apps.notifications.services import notify_cava_deleted

        ws = getattr(getattr(instance, "storage", None), "workspace", None)
        label = instance.name or instance.code or str(instance.pk)
        pk = instance.pk
        instance.delete()
        log_crud(
            workspace=ws,
            section=SECTION.CAVA,
            action="delete",
            verb="eliminó la cava",
            label=label,
            actor=self.request.user,
            resource_id=pk,
            resource_code=label,
        )
        notify_cava_deleted(
            workspace=ws,
            label=label,
            resource_id=pk,
            actor=self.request.user,
        )

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
        workspace = cava.storage.workspace
        slot_count = sum(1 for _ in iter_slots(cava))
        if slot_count < 1:
            return Response(
                {"detail": "Agregue al menos un rack."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Mismo tipo: no otra generación de cava en el owner mientras hay una activa.
        with transaction.atomic():
            Workspace.objects.select_for_update().filter(pk=workspace.pk).exists()
            if storage_models.CavaGenerationJob.objects.filter(
                workspace=workspace,
                status__in=(active.PENDING, active.RUNNING),
            ).exists():
                return Response(
                    {"detail": "En proceso. Puedes continuar mientras se crean."},
                    status=status.HTTP_409_CONFLICT,
                )
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
        try:
            from apps.audit.models import ActivityBatch, ActivitySection
            from apps.audit.services.view_log import log_bulk
            from apps.notifications.services import notify_positions_generated

            notify_positions_generated(
                cava=job.cava,
                created_count=result["created"],
                actor=self.request.user,
            )
            items = result.get("created_items") or []
            if items:
                cava_name = job.cava.name or job.cava.code or str(job.cava_id)
                actor = self.request.user
                from apps.core.utils.display_name import user_display_name
                actor_name = user_display_name(actor)
                noun = "posición" if len(items) == 1 else "posiciones"
                log_bulk(
                    workspace=job.workspace,
                    section=ActivitySection.POSITION,
                    action="generate",
                    kind=ActivityBatch.Kind.GENERATE,
                    summary=f"{actor_name} generó {len(items)} {noun} en {cava_name}",
                    items=items,
                    actor=actor,
                )
        except Exception:
            logger.exception(
                "No se pudo notificar generación de cava %s", job.cava_id
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

        from apps.audit.models import ActivitySection
        from apps.audit.services.view_log import log_unit
        from apps.notifications.services import notify_cava_hilera_push
        from apps.workspaces.utils.tenant import get_workspace_for_request

        user = request.user
        from apps.core.utils.display_name import user_display_name
        actor_name = user_display_name(user)
        verb = "activó" if is_push else "desactivó"
        cava_label = cava.name or cava.code or str(cava.pk)
        ws = get_workspace_for_request(request) or getattr(
            getattr(cava, "storage", None), "workspace", None
        )
        log_unit(
            workspace=ws,
            section=ActivitySection.CAVA,
            action="set_hilera_push",
            summary=(
                f"{actor_name} {verb} push en hilera {hall} "
                f"de {cava_label} ({updated} posiciones)"
            ),
            actor=user,
            resource_id=cava.pk,
            resource_code=cava_label,
            changes={
                "hall": hall,
                "racks": racks,
                "is_push": is_push,
                "updated": updated,
            },
        )
        notify_cava_hilera_push(
            cava=cava,
            hall=hall,
            updated=updated,
            is_push=is_push,
            actor=user,
        )
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

    def perform_update(self, serializer):
        """Cambios manuales (estado/flags). Cascadas desde bins no pasan por aquí."""
        instance = self.get_object()
        old_status = instance.status
        old_flags = (instance.is_active, instance.is_picking, instance.is_push)
        updated = serializer.save()
        user = self.request.user
        new_status = updated.status
        from apps.core.utils.display_name import user_display_name
        actor_name = user_display_name(user)
        from apps.audit.models import ActivitySection
        from apps.audit.services.view_log import log_unit
        from apps.storage.models import PositionMovement, PositionMovementType

        ws = getattr(updated, "workspace", None)
        if old_status != new_status:
            from apps.notifications.services import notify_position_status

            notify_position_status(
                position=updated, new_status=new_status, actor=user
            )
            PositionMovement.log_movement(
                updated,
                PositionMovementType.STATUS_CHANGED,
                old_status=old_status,
                new_status=new_status,
            )
            log_unit(
                workspace=ws,
                section=ActivitySection.POSITION,
                action="status",
                summary=f"{actor_name} cambió la posición {updated.code} a {new_status}",
                actor=user,
                resource_id=updated.pk,
                resource_code=updated.code,
            )
        else:
            from apps.notifications.services import notify_position_updated

            notify_position_updated(position=updated, actor=user)
            new_flags = (updated.is_active, updated.is_picking, updated.is_push)
            if old_flags != new_flags:
                PositionMovement.log_movement(
                    updated,
                    PositionMovementType.FLAGS_CHANGED,
                    changes={
                        "is_active": updated.is_active,
                        "is_picking": updated.is_picking,
                        "is_push": updated.is_push,
                    },
                )
            log_unit(
                workspace=ws,
                section=ActivitySection.POSITION,
                action="update",
                summary=f"{actor_name} actualizó la posición {updated.code}",
                actor=user,
                resource_id=updated.pk,
                resource_code=updated.code,
            )

    def perform_destroy(self, instance):
        from apps.notifications.services import notify_position_deleted
        from apps.audit.models import ActivitySection
        from apps.audit.services.view_log import log_unit

        workspace = getattr(instance, "workspace", None)
        if workspace is None:
            cava = getattr(instance, "cava", None)
            storage = getattr(cava, "storage", None) if cava else None
            workspace = getattr(storage, "workspace", None)
        code = getattr(instance, "code", "") or str(instance.pk)
        pk = instance.pk
        user = self.request.user
        from apps.core.utils.display_name import user_display_name
        actor_name = user_display_name(user)
        instance.delete()
        notify_position_deleted(
            workspace=workspace,
            code=code,
            resource_id=pk,
            actor=user,
        )
        log_unit(
            workspace=workspace,
            section=ActivitySection.POSITION,
            action="delete",
            summary=f"{actor_name} eliminó la posición {code}",
            actor=user,
            resource_id=pk,
            resource_code=code,
        )

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

    BULK_MAX_IDS = 10000

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
        total = qs.count()
        id_list = list(qs.values_list("id", flat=True)[: self.BULK_MAX_IDS])
        truncated = total > len(id_list)
        payload = {
            "ids": id_list,
            "count": len(id_list),
            "total": total,
            "truncated": truncated,
        }
        if truncated:
            payload["detail"] = (
                f"Se seleccionaron {len(id_list)} de {total} "
                f"(máximo {self.BULK_MAX_IDS} por operación)."
            )
        return Response(payload)

    def _start_position_bulk(self, request, *, kind, runner, params=None):
        from apps.common.models import BulkOperationJob
        from apps.common.tasks import run_bulk_operation_job
        from apps.common.utils.bulk_jobs import (
            BulkJobInProgress,
            USER_DETAIL_ASYNC,
            serialize_bulk_job,
            start_bulk_operation,
        )

        if is_client_user(request.user):
            return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)

        ids, error = self._parse_bulk_ids(request)
        if error is not None:
            return error

        ws = get_workspace_for_request(request)
        if ws is None:
            return Response(
                {"detail": "No se pudo completar la operación."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Conservar orden de ids del payload (empaquetado por etiqueta).
        scoped = set(
            self.scope_to_workspace(
                storage_models.Position.objects.filter(id__in=ids)
            ).values_list("id", flat=True)
        )
        scoped_ids = [i for i in ids if i in scoped]
        if not scoped_ids:
            return Response(
                {"detail": "No se encontraron posiciones."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            job, ran_async = start_bulk_operation(
                workspace=ws,
                kind=kind,
                item_ids=scoped_ids,
                runner=runner,
                celery_task=run_bulk_operation_job,
                params=params,
            )
        except BulkJobInProgress:
            return Response(
                {"detail": USER_DETAIL_ASYNC},
                status=status.HTTP_409_CONFLICT,
            )
        payload = serialize_bulk_job(job, async_=ran_async)
        if ran_async:
            return Response(payload, status=status.HTTP_202_ACCEPTED)
        if job.status == BulkOperationJob.Status.FAILED:
            return Response(payload, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)

    @action(detail=False, methods=["delete"], url_path="bulk-delete")
    def bulk_delete(self, request):
        """Elimina posiciones libres en masa (async si hay más de una)."""
        from apps.common.models import BulkOperationJob
        from apps.storage.services_bulk import run_position_delete

        return self._start_position_bulk(
            request,
            kind=BulkOperationJob.Kind.POSITION_DELETE,
            runner=run_position_delete,
        )

    @action(detail=False, methods=["post"], url_path="bulk-regenerate-tags")
    def bulk_regenerate_tags(self, request):
        """Regenera QR/ZPL de posiciones (async si hay más de una)."""
        from apps.common.models import BulkOperationJob
        from apps.storage.services_bulk import run_position_regenerate_tags

        return self._start_position_bulk(
            request,
            kind=BulkOperationJob.Kind.POSITION_REGENERATE_TAGS,
            runner=run_position_regenerate_tags,
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
            if touched:
                from apps.notifications.models import Notification
                from apps.notifications.services import notify_positions_bulk

                ws = get_workspace_for_request(request)
                noun = "posición" if touched == 1 else "posiciones"
                from apps.core.utils.display_name import user_display_name
                actor_name = user_display_name(request.user)
                notify_positions_bulk(
                    workspace=ws,
                    event=Notification.Event.UPDATED,
                    message=f"{actor_name} desactivó {touched} {noun}",
                    count=touched,
                    actor=request.user,
                )
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
        if touched:
            from apps.audit.models import ActivityBatch, ActivitySection
            from apps.audit.services.view_log import log_bulk
            from apps.notifications.models import Notification
            from apps.notifications.services import notify_positions_bulk

            ws = get_workspace_for_request(request)
            noun = "posición" if touched == 1 else "posiciones"
            from apps.core.utils.display_name import user_display_name
            actor_name = user_display_name(request.user)
            message = f"{actor_name} actualizó {touched} {noun}"
            notify_positions_bulk(
                workspace=ws,
                event=Notification.Event.UPDATED,
                message=message,
                count=touched,
                actor=request.user,
            )
            items = [
                {"resource_id": pk, "resource_code": code or ""}
                for pk, code in qs.values_list("id", "code")[: max(touched, 1)]
            ]
            if items:
                log_bulk(
                    workspace=ws,
                    section=ActivitySection.POSITION,
                    action="bulk_flags",
                    kind=ActivityBatch.Kind.BULK_FLAGS,
                    summary=message,
                    items=items,
                    actor=request.user,
                )
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
        """Imprime etiquetas ZPL de posiciones (async si hay más de una).

        `per_label` (1–4): cuántas posiciones distintas van en cada etiqueta 4×2.
        La última etiqueta puede quedar incompleta.
        """
        from apps.common.models import BulkOperationJob
        from apps.storage.services_bulk import run_position_print

        ws = get_workspace_for_request(request)
        if not printer_is_configured(ws):
            return Response(
                {"detail": USER_PRINTER_NOT_CONFIGURED},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw = request.data.get("per_label", 1)
        try:
            per_label = int(raw)
        except (TypeError, ValueError):
            per_label = 1
        per_label = max(1, min(per_label, 4))
        same_copies = self._optional_bool(request.data, "same_position_copies")
        if same_copies is None:
            same_copies = False

        return self._start_position_bulk(
            request,
            kind=BulkOperationJob.Kind.POSITION_PRINT,
            runner=run_position_print,
            params={
                "per_label": per_label,
                "same_position_copies": bool(same_copies),
            },
        )

    @action(detail=True, methods=['post'])
    def print(self, request, pk=None):
        """Imprime la etiqueta ZPL de una posición."""
        if is_client_user(request.user):
            return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)

        from apps.storage.services import generate_position_tag_zpl

        position = self.get_object()
        if not position.code:
            return Response(
                {
                    "success": False,
                    "message": "No hay etiqueta para imprimir.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        workspace = getattr(position, "workspace", None) or get_workspace_for_request(
            request
        )
        if not printer_is_configured(workspace):
            return Response(
                {"success": False, "message": USER_PRINTER_NOT_CONFIGURED},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # ZPL fresco según medida/DPI del owner (no el ZPL guardado antiguo).
        zpl = generate_position_tag_zpl(position)
        if not zpl or not print_to_zebra(zpl, workspace=workspace):
            return Response(
                {"success": False, "message": USER_PRINT_FAILED},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "success": True,
            "message": "Etiqueta enviada a impresión.",
        })

    @action(detail=True, methods=["get"], url_path="movements")
    def movements(self, request, pk=None):
        """Historial paginado de la posición (lazy desde la pestaña del modal)."""
        from apps.common.utils.movements_pagination import paginated_movements_response

        position = self.get_object()
        movements = storage_models.PositionMovement.objects.filter(
            position=position
        ).select_related("created_by", "bin").order_by("-created_at")
        return paginated_movements_response(
            request,
            queryset=movements,
            serializer_class=serializers.PositionMovementSerializer,
            meta={"position_id": position.id, "position_code": position.code},
        )

    @action(detail=True, methods=['post'])
    def regenerate_tag(self, request, pk=None):
        """Regenera QR/ZPL de una posición (sync)."""
        if is_client_user(request.user):
            return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)

        position = self.get_object()
        position.refresh_label(persist=True)
        position.refresh_from_db(fields=["qr_code_image", "zpl_code", "updated_at"])

        from apps.audit.models import ActivitySection
        from apps.audit.services.view_log import log_unit
        from apps.common.utils.media_layout import media_url_with_bust
        from apps.notifications.services import notify_position_updated
        from apps.storage.models import PositionMovement, PositionMovementType
        from apps.workspaces.utils.tenant import get_workspace_for_request

        user = request.user
        from apps.core.utils.display_name import user_display_name
        actor_name = user_display_name(user)
        code = position.code or str(position.pk)
        ws = get_workspace_for_request(request)
        log_unit(
            workspace=ws,
            section=ActivitySection.POSITION,
            action="update",
            summary=f"{actor_name} regeneró la etiqueta de {code}",
            actor=user,
            resource_id=position.pk,
            resource_code=code,
        )
        PositionMovement.log_movement(
            position, PositionMovementType.LABEL_REGENERATED
        )
        notify_position_updated(position=position, actor=user)

        qr_url = media_url_with_bust(
            position.qr_code_image,
            request=request,
            version=position.updated_at,
        )
        return Response({
            "success": True,
            "message": "Etiqueta regenerada.",
            "qr_code_image": qr_url,
            "zpl_code": position.zpl_code or "",
        })
