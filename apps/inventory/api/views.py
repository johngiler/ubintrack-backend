from rest_framework import viewsets, filters, status
from apps.core.utils.user_roles import get_user_client, get_user_client_id, is_client_user, can_manage_as_administrator
from rest_framework.permissions import IsAuthenticated, DjangoModelPermissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django_filters.rest_framework import DjangoFilterBackend
from django.db import transaction
from django.http import HttpResponse
from datetime import datetime

from apps.inventory import models as inventory_models
from apps.inventory.services import export_bin_entries_excel, export_products_excel
from apps.common.utils.permissions_mixins import ClientScopeMixin, StrictDjangoModelPermissions
from apps.core import choices
from apps.common.utils.zebra import print_to_zebra

from . import serializers


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = inventory_models.Category.objects.all()
    serializer_class = serializers.CategorySerializer


class ProductViewSet(ClientScopeMixin, viewsets.ModelViewSet):
    queryset = inventory_models.Product.objects.all().order_by('-created_at')
    serializer_class = serializers.ProductSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['client', 'category', 'rotation_type',
                        'condition', 'is_picking', 'is_active']
    search_fields = ['name', 'client_sku', 'client__name']
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]

    def get_queryset(self):
        return self.scope_to_user(super().get_queryset())

    def perform_create(self, serializer):
        from apps.audit.services.crud_hooks import SECTION, log_crud

        user = self.request.user
        client = None
        if is_client_user(user):
            client = get_user_client(user)
        obj = serializer.save(
            client=client or serializer.validated_data.get('client'))
        ws = getattr(getattr(obj, "client", None), "workspace", None)
        label = obj.name or obj.client_sku or str(obj.pk)
        log_crud(
            workspace=ws,
            section=SECTION.PRODUCT,
            action="create",
            verb="creó el producto",
            label=label,
            actor=user,
            resource_id=obj.pk,
            resource_code=label,
        )

    def perform_update(self, serializer):
        from apps.audit.services.crud_hooks import SECTION, log_crud

        user = self.request.user
        if is_client_user(user):
            obj = serializer.save(client=get_user_client(user))
        else:
            obj = serializer.save()
        ws = getattr(getattr(obj, "client", None), "workspace", None)
        label = obj.name or obj.client_sku or str(obj.pk)
        log_crud(
            workspace=ws,
            section=SECTION.PRODUCT,
            action="update",
            verb="actualizó el producto",
            label=label,
            actor=user,
            resource_id=obj.pk,
            resource_code=label,
        )

    def perform_destroy(self, instance):
        from apps.audit.services.crud_hooks import SECTION, log_crud

        ws = getattr(getattr(instance, "client", None), "workspace", None)
        label = instance.name or instance.client_sku or str(instance.pk)
        pk = instance.pk
        instance.delete()
        log_crud(
            workspace=ws,
            section=SECTION.PRODUCT,
            action="delete",
            verb="eliminó el producto",
            label=label,
            actor=self.request.user,
            resource_id=pk,
            resource_code=label,
        )

    @action(detail=False, methods=['get'])
    def export_excel(self, request):
        """Exporta productos a Excel respetando filtros y búsqueda de la lista."""
        queryset = self.filter_queryset(self.get_queryset())
        output = export_products_excel(queryset)
        filename = f"Productos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xls"
        response = HttpResponse(
            output.getvalue(),
            content_type='application/vnd.ms-excel',
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class BinViewSet(ClientScopeMixin, viewsets.ModelViewSet):
    queryset = inventory_models.Bin.objects.all().order_by('-created_at')
    serializer_class = serializers.BinSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['status', 'is_active']
    # Buscar por código de bin o SKU del producto
    search_fields = [
        'code', 'bin_products__entry_product__product__client_sku']
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]
    BULK_MAX_IDS = 10000

    def get_queryset(self):
        return self.scope_to_user(super().get_queryset()).distinct()

    def filter_queryset(self, queryset):
        # Verificar si es búsqueda por entrada antes de aplicar filtros
        search = self.request.query_params.get('search', '')
        # Detectar si busca por código de entrada (contiene "ENTRADA" o empieza con código de cliente)
        if search and 'ENTRADA' in search.upper() and not search.upper().startswith('BIN-'):
            # Si busca un código de entrada, filtrar por entradas relacionadas (sin usar SearchFilter)
            queryset = queryset.filter(
                bin_products__entry_product__entry__code__icontains=search
            ).distinct()
            # Aplicar solo DjangoFilterBackend, no SearchFilter
            for backend in self.filter_backends:
                if backend != filters.SearchFilter:
                    queryset = backend().filter_queryset(self.request, queryset, self)
        else:
            queryset = super().filter_queryset(queryset)

        has_position = (self.request.query_params.get("has_position") or "").strip().lower()
        if has_position in ("true", "1", "yes"):
            queryset = queryset.filter(position__isnull=False)
        elif has_position in ("false", "0", "no"):
            queryset = queryset.filter(position__isnull=True)
        return queryset

    def _parse_bulk_ids(self, request):
        raw = request.data.get("ids") or []
        if not isinstance(raw, list) or not raw:
            return None, Response(
                {"detail": "Seleccione al menos un bin."},
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
                {"detail": f"Máximo {self.BULK_MAX_IDS} bins por operación."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return ids, None

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

    def _start_bin_bulk(self, request, *, kind, runner):
        from apps.common.models import BulkOperationJob
        from apps.common.tasks import run_bulk_operation_job
        from apps.common.utils.bulk_jobs import serialize_bulk_job, start_bulk_operation
        from apps.workspaces.utils.tenant import get_workspace_for_request

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

        scoped_ids = list(
            self.scope_to_user(
                inventory_models.Bin.objects.filter(id__in=ids, workspace=ws)
            )
            .distinct()
            .values_list("id", flat=True)
        )
        if not scoped_ids:
            return Response(
                {"detail": "No se encontraron bins."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        job, ran_async = start_bulk_operation(
            workspace=ws,
            kind=kind,
            item_ids=scoped_ids,
            runner=runner,
            celery_task=run_bulk_operation_job,
        )
        payload = serialize_bulk_job(job, async_=ran_async)
        if ran_async:
            return Response(payload, status=status.HTTP_202_ACCEPTED)
        if job.status == BulkOperationJob.Status.FAILED:
            return Response(payload, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)

    @action(detail=False, methods=["delete"], url_path="bulk-delete")
    def bulk_delete(self, request):
        """Elimina bins en masa (async si hay más de uno)."""
        from apps.common.models import BulkOperationJob
        from apps.inventory.services_bulk import run_bin_delete

        return self._start_bin_bulk(
            request,
            kind=BulkOperationJob.Kind.BIN_DELETE,
            runner=run_bin_delete,
        )

    @action(detail=False, methods=["post"], url_path="bulk-regenerate-tags")
    def bulk_regenerate_tags(self, request):
        """Regenera etiquetas/ZPL de bins (async si hay más de uno)."""
        from apps.common.models import BulkOperationJob
        from apps.inventory.services_bulk import run_bin_regenerate_tags

        return self._start_bin_bulk(
            request,
            kind=BulkOperationJob.Kind.BIN_REGENERATE_TAGS,
            runner=run_bin_regenerate_tags,
        )

    @action(detail=False, methods=["post"], url_path="bulk-update-flags")
    def bulk_update_flags(self, request):
        """Activa / desactiva bins en masa (solo is_active)."""
        if is_client_user(request.user):
            return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)

        from apps.order.models import ExitProduct

        ids, error = self._parse_bulk_ids(request)
        if error is not None:
            return error

        is_active = self._optional_bool(request.data, "is_active")
        if is_active is None:
            return Response(
                {"detail": "Indique el estado activo/inactivo."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = self.scope_to_user(
            inventory_models.Bin.objects.filter(id__in=ids)
        ).distinct()
        if not qs.exists():
            return Response(
                {"detail": "No se encontraron bins."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated = 0
        blocked = 0
        if is_active is True:
            updated = qs.filter(is_active=False).update(is_active=True)
        else:
            for bin_obj in qs.filter(is_active=True).iterator():
                if bin_obj.status == choices.BinStatus.ALMACENADO:
                    has_exit_products = ExitProduct.objects.filter(
                        bin_product__bin=bin_obj,
                        exit_product_group__exit__status__in=[
                            "pendiente",
                            "en_progreso",
                        ],
                    ).exists()
                    if has_exit_products:
                        blocked += 1
                        continue
                inventory_models.Bin.objects.filter(pk=bin_obj.pk).update(
                    is_active=False
                )
                updated += 1

        parts = [
            f"Se {'activaron' if is_active else 'desactivaron'} {updated} bins."
        ]
        if blocked:
            parts.append(f"{blocked} con salidas asociadas.")
        if updated:
            from apps.audit.models import ActivityBatch, ActivitySection
            from apps.audit.services.view_log import log_bulk
            from apps.notifications.models import Notification
            from apps.notifications.services import notify_bins_bulk
            from apps.workspaces.utils.tenant import get_workspace_for_request

            ws = get_workspace_for_request(request)
            verb = "activó" if is_active else "desactivó"
            noun = "bin" if updated == 1 else "bins"
            actor_name = (
                (getattr(request.user, "get_full_name", lambda: "")() or "").strip()
                or getattr(request.user, "username", None)
                or "Usuario"
            )
            message = f"{actor_name} {verb} {updated} {noun}"
            notify_bins_bulk(
                workspace=ws,
                event=Notification.Event.UPDATED,
                message=message,
                count=updated,
                actor=request.user,
            )
            items = [
                {"resource_id": pk, "resource_code": code or ""}
                for pk, code in qs.values_list("id", "code")[: max(updated, 1)]
            ]
            if items:
                log_bulk(
                    workspace=ws,
                    section=ActivitySection.BIN,
                    action="bulk_flags",
                    kind=ActivityBatch.Kind.BULK_FLAGS,
                    summary=message,
                    items=items,
                    actor=request.user,
                )
        return Response(
            {
                "success": True,
                "updated": updated,
                "blocked": blocked,
                "message": " ".join(parts),
            }
        )

    @action(detail=False, methods=["post"], url_path="bulk-print")
    def bulk_print(self, request):
        """Imprime etiquetas ZPL de bins (async si hay más de uno)."""
        from apps.common.models import BulkOperationJob
        from apps.inventory.services_bulk import run_bin_print

        return self._start_bin_bulk(
            request,
            kind=BulkOperationJob.Kind.BIN_PRINT,
            runner=run_bin_print,
        )

    def perform_create(self, serializer):
        user = self.request.user
        client = None
        if is_client_user(user):
            client = get_user_client(user)
        bin_obj = serializer.save(
            client=client or serializer.validated_data.get('client'))
        from apps.notifications.services import notify_bin_created
        from apps.audit.models import ActivitySection
        from apps.audit.services.view_log import log_unit

        notify_bin_created(bin_obj=bin_obj, actor=user)
        actor_name = (
            (getattr(user, "get_full_name", lambda: "")() or "").strip()
            or getattr(user, "username", None)
            or "Usuario"
        )
        ws = getattr(bin_obj, "workspace", None) or getattr(
            getattr(bin_obj, "client", None), "workspace", None
        )
        log_unit(
            workspace=ws,
            section=ActivitySection.BIN,
            action="create",
            summary=f"{actor_name} creó el bin {bin_obj.code}",
            actor=user,
            resource_id=bin_obj.pk,
            resource_code=bin_obj.code,
        )

    @transaction.atomic
    def perform_update(self, serializer):
        from rest_framework.exceptions import ValidationError
        from apps.order.models import ExitProduct

        instance = self.get_object()
        user = self.request.user

        # VALIDACIÓN: No se puede desactivar un bin ALMACENADO que tiene salidas asociadas
        new_is_active = serializer.validated_data.get(
            'is_active', instance.is_active)
        if instance.is_active and not new_is_active:  # Intentando desactivar
            if instance.status == choices.BinStatus.ALMACENADO:
                has_exit_products = ExitProduct.objects.filter(
                    bin_product__bin=instance,
                    exit_product_group__exit__status__in=[
                        'pendiente', 'en_progreso']
                ).exists()

                if has_exit_products:
                    raise ValidationError({
                        'is_active': 'No se puede desactivar este bin porque tiene salidas asociadas. '
                                     'Primero elimine o complete las salidas que usan este bin.'
                    })

        old_status = instance.status

        # Seguridad adicional: si es usuario cliente y quiere marcar como almacenado, exigir QR válido
        if is_client_user(user):
            new_status = serializer.validated_data.get(
                'status', instance.status)
            if new_status == choices.BinStatus.ALMACENADO:
                qr_tag = self.request.data.get('qr_tag')
                qr_position = self.request.data.get('qr_position')
                # Requerir coincidencia estricta con códigos reales
                if not (qr_tag and qr_position and qr_tag == instance.code and (
                        instance.position and qr_position == instance.position.code)):
                    from rest_framework.exceptions import PermissionDenied
                    raise PermissionDenied(
                        'Validación QR requerida para almacenar el bin.')

            bin_obj = serializer.save(client=get_user_client(user))
        else:
            bin_obj = serializer.save()

        from apps.audit.models import ActivitySection
        from apps.audit.services.view_log import log_unit

        actor_name = (
            (getattr(user, "get_full_name", lambda: "")() or "").strip()
            or getattr(user, "username", None)
            or "Usuario"
        )
        ws = getattr(bin_obj, "workspace", None) or getattr(
            getattr(bin_obj, "client", None), "workspace", None
        )
        # Posición libre/ocupado por cascada de bin: no notificar posición (solo el bin).
        if old_status != bin_obj.status:
            from apps.notifications.services import notify_bin_status

            notify_bin_status(
                bin_obj=bin_obj, new_status=bin_obj.status, actor=user
            )
            log_unit(
                workspace=ws,
                section=ActivitySection.BIN,
                action="status",
                summary=f"{actor_name} cambió el bin {bin_obj.code} a {bin_obj.status}",
                actor=user,
                resource_id=bin_obj.pk,
                resource_code=bin_obj.code,
            )
        else:
            from apps.notifications.services import notify_bin_updated

            notify_bin_updated(bin_obj=bin_obj, actor=user)
            log_unit(
                workspace=ws,
                section=ActivitySection.BIN,
                action="update",
                summary=f"{actor_name} actualizó el bin {bin_obj.code}",
                actor=user,
                resource_id=bin_obj.pk,
                resource_code=bin_obj.code,
            )

    @action(detail=True, methods=['post'], url_path='store-with-qr')
    @transaction.atomic
    def store_with_qr(self, request, pk=None):
        """
        Cambia el bin a ALMACENADO exigiendo validación QR (tag y posición).
        Pensado para clientes; usuarios internos también pueden usarlo.
        Operación atómica para garantizar consistencia.
        """
        bin_instance = self.get_object()

        qr_tag = request.data.get('qr_tag')
        qr_position = request.data.get('qr_position')

        if not (qr_tag and qr_position):
            return Response({
                'success': False,
                'message': 'Se requieren qr_tag y qr_position'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Validar coincidencias exactas
        if qr_tag != bin_instance.code:
            return Response({
                'success': False,
                'message': 'El QR del tag no coincide con el código del bin'
            }, status=status.HTTP_400_BAD_REQUEST)

        if not bin_instance.position or qr_position != bin_instance.position.code:
            return Response({
                'success': False,
                'message': 'El QR de la posición no coincide con la posición del bin'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Actualizar estado y marcas (todo en la misma transacción)
        bin_instance.status = choices.BinStatus.ALMACENADO
        bin_instance.is_processed = True
        bin_instance.was_qr_validated = True
        bin_instance.save()

        # Asegurar estado de la posición (cascada: no campanita de posición; sí timeline)
        if bin_instance.position:
            from apps.storage.models import Position, PositionMovement, PositionMovementType
            from django.utils import timezone

            pos = bin_instance.position
            old_pos_status = pos.status
            Position.objects.filter(pk=pos.pk).update(
                status=choices.PositionStatus.OCUPADO,
                updated_at=timezone.now()
            )
            if old_pos_status != choices.PositionStatus.OCUPADO:
                PositionMovement.log_movement(
                    pos,
                    PositionMovementType.STATUS_CHANGED,
                    old_status=old_pos_status,
                    new_status=choices.PositionStatus.OCUPADO,
                    bin=bin_instance,
                    bin_code=bin_instance.code,
                    notes="Almacenamiento de bin",
                )
                PositionMovement.log_movement(
                    pos,
                    PositionMovementType.BIN_LINKED,
                    bin=bin_instance,
                    bin_code=bin_instance.code,
                )

        from apps.notifications.services import notify_bin_status
        from apps.audit.models import ActivitySection
        from apps.audit.services.view_log import log_unit

        notify_bin_status(
            bin_obj=bin_instance,
            new_status=bin_instance.status,
            actor=request.user,
        )
        user = request.user
        actor_name = (
            (getattr(user, "get_full_name", lambda: "")() or "").strip()
            or getattr(user, "username", None)
            or "Usuario"
        )
        ws = getattr(bin_instance, "workspace", None) or getattr(
            getattr(bin_instance, "client", None), "workspace", None
        )
        log_unit(
            workspace=ws,
            section=ActivitySection.BIN,
            action="status",
            summary=f"{actor_name} cambió el bin {bin_instance.code} a {bin_instance.status}",
            actor=user,
            resource_id=bin_instance.pk,
            resource_code=bin_instance.code,
        )

        return Response({
            'success': True,
            'message': f"Bin {bin_instance.code} almacenado exitosamente",
        })

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError
        from apps.order.models import ExitProduct

        # VALIDACIÓN: No se puede eliminar un bin ALMACENADO si tiene salidas asociadas
        if instance.status == choices.BinStatus.ALMACENADO:
            # Verificar si tiene ExitProducts asociados (pendientes o en progreso)
            has_exit_products = ExitProduct.objects.filter(
                bin_product__bin=instance,
                exit_product_group__exit__status__in=[
                    'pendiente', 'en_progreso']
            ).exists()

            if has_exit_products:
                raise ValidationError(
                    'No se puede eliminar este bin porque tiene salidas asociadas. '
                    'Primero elimine o complete las salidas que usan este bin.'
                )

        from apps.notifications.services import notify_bin_deleted
        from apps.audit.models import ActivitySection
        from apps.audit.services.view_log import log_unit

        workspace = getattr(instance, "workspace", None)
        if workspace is None:
            workspace = getattr(getattr(instance, "client", None), "workspace", None)
        code = getattr(instance, "code", "") or str(instance.pk)
        pk = instance.pk
        user = self.request.user
        actor_name = (
            (getattr(user, "get_full_name", lambda: "")() or "").strip()
            or getattr(user, "username", None)
            or "Usuario"
        )

        # Liberar la posición si existe (cascada: timeline sí, campanita no)
        if instance.position:
            from apps.storage.models import Position, PositionMovement, PositionMovementType
            from django.utils import timezone

            pos = instance.position
            old_pos_status = pos.status
            Position.objects.filter(pk=pos.pk).update(
                status=choices.PositionStatus.LIBRE,
                updated_at=timezone.now()
            )
            if old_pos_status != choices.PositionStatus.LIBRE:
                PositionMovement.log_movement(
                    pos,
                    PositionMovementType.STATUS_CHANGED,
                    old_status=old_pos_status,
                    new_status=choices.PositionStatus.LIBRE,
                    bin_code=code,
                    notes="Eliminación de bin",
                )
            PositionMovement.log_movement(
                pos,
                PositionMovementType.BIN_UNLINKED,
                bin_code=code,
            )
        instance.delete()
        notify_bin_deleted(
            workspace=workspace,
            code=code,
            resource_id=pk,
            actor=user,
        )
        log_unit(
            workspace=workspace,
            section=ActivitySection.BIN,
            action="delete",
            summary=f"{actor_name} eliminó el bin {code}",
            actor=user,
            resource_id=pk,
            resource_code=code,
        )

    @action(detail=False, methods=['get'])
    def export_excel(self, request):
        """
        Export bin entries to Excel file
        """
        queryset = self.get_queryset()

        # Apply filters if any
        queryset = self.filter_queryset(queryset)

        # Prefetch related data to optimize queries
        queryset = queryset.select_related(
            'client', 'entry', 'position'
        ).prefetch_related('bin_products__entry_product__product')

        # Generate Excel file
        output = export_bin_entries_excel(queryset)

        # Prepare response
        filename = f"Bins_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xls"
        response = HttpResponse(
            output.getvalue(),
            content_type='application/vnd.ms-excel'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        return response

    @action(detail=False, methods=['get'], url_path='criterios-almacenamiento')
    def criterios_almacenamiento(self, request):
        """Lista los criterios de almacenamiento disponibles."""
        from apps.inventory.mass_store import list_storage_criteria

        return Response(list_storage_criteria())

    @action(detail=False, methods=['post'], url_path='almacenar-masivo')
    @transaction.atomic
    def almacenar_masivo(self, request):
        """
        Almacenamiento masivo por criterios de almacenamiento: validar → sugerir → aplicar.
        Body: { strategy: "push"|"compact_sku_batch", bin_ids: [1,2,...], confirm?: bool, assignments?: [...] }
        - confirm=false o ausente: solo sugiere (preview)
        - confirm=true: aplica sugerencia o assignments del preview
        """
        from django.core.exceptions import ValidationError as DjangoValidationError
        from apps.inventory.mass_store import apply_mass_store, suggest_mass_store
        from apps.workspaces.utils.tenant import get_workspace_for_request

        user = request.user
        if not can_manage_as_administrator(user):
            return Response(
                {'success': False, 'message': 'No autorizado.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        ws = get_workspace_for_request(request)
        if ws is None:
            return Response(
                {'success': False, 'message': 'No se pudo completar la operación.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        strategy = (request.data.get('strategy') or 'push').strip()
        bin_ids = request.data.get('bin_ids') or []
        confirm = bool(request.data.get('confirm'))
        assignments = request.data.get('assignments')
        params = request.data.get('params') or {}

        if not isinstance(bin_ids, list) or not bin_ids:
            return Response(
                {'success': False, 'message': 'Seleccione al menos un bin.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            bin_ids = [int(x) for x in bin_ids]
        except (TypeError, ValueError):
            return Response(
                {'success': False, 'message': 'bin_ids inválidos.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            if not confirm:
                planned = suggest_mass_store(
                    bin_ids=bin_ids,
                    workspace_id=ws.id,
                    strategy=strategy,
                    params=params,
                )
                return Response({
                    'success': True,
                    'preview': True,
                    'strategy': strategy,
                    'assignments': [
                        {
                            'bin_id': a.bin_id,
                            'position_id': a.position_id,
                            'bin_code': a.bin_code,
                            'position_code': a.position_code,
                            'conduit_key': a.conduit_key,
                        }
                        for a in planned
                    ],
                    'message': f'{len(planned)} posiciones sugeridas.',
                })

            result = apply_mass_store(
                bin_ids=bin_ids,
                workspace_id=ws.id,
                strategy=strategy,
                params=params,
                user=user,
                assignments=assignments,
            )
            return Response(result)
        except DjangoValidationError as exc:
            messages = getattr(exc, 'messages', None) or [str(exc)]
            return Response(
                {'success': False, 'message': messages[0] if messages else 'No se pudo completar la operación.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=['post'])
    def print(self, request, pk=None):
        """Imprime la etiqueta ZPL de un bin."""
        if is_client_user(request.user):
            return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)

        bin_instance = self.get_object()
        if not bin_instance.zpl_code:
            return Response(
                {
                    "success": False,
                    "message": "No hay etiqueta para imprimir.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not print_to_zebra(bin_instance.zpl_code):
            return Response(
                {
                    "success": False,
                    "message": "No se pudo imprimir la etiqueta. Verifique la impresora.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "success": True,
            "message": "Etiqueta enviada a impresión.",
        })

    @action(detail=True, methods=['post'])
    def regenerate_tag(self, request, pk=None):
        """Regenera etiqueta/ZPL de un bin (sync)."""
        if is_client_user(request.user):
            return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)

        bin_instance = self.get_object()
        success = bin_instance.generate_tag()
        if success:
            bin_instance.refresh_from_db(fields=["tag_image", "zpl_code", "updated_at"])
            from apps.common.utils.media_layout import media_url_with_bust

            tag_url = media_url_with_bust(
                bin_instance.tag_image,
                request=request,
                version=bin_instance.updated_at,
            )
            return Response({
                "success": True,
                "message": "Etiqueta regenerada.",
                "tag_image": tag_url,
                "zpl_code": bin_instance.zpl_code or "",
            })
        return Response(
            {
                "success": False,
                "message": "No se pudo regenerar la etiqueta.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=True, methods=['get'], url_path='movements')
    def movements(self, request, pk=None):
        """Historial paginado del bin (lazy desde la pestaña del modal)."""
        from apps.common.utils.movements_pagination import paginated_movements_response

        bin_instance = self.get_object()
        movements = inventory_models.BinMovement.objects.filter(
            bin=bin_instance
        ).select_related(
            'created_by',
            'exit',
            'position',
            'bin__entry',
        ).prefetch_related(
            'bin__bin_products__entry_product__product'
        ).order_by('-created_at')

        return paginated_movements_response(
            request,
            queryset=movements,
            serializer_class=serializers.BinMovementSerializer,
            meta={"bin_id": bin_instance.id, "bin_code": bin_instance.code},
        )


class BinProductViewSet(ClientScopeMixin, viewsets.ModelViewSet):
    queryset = inventory_models.BinProduct.objects.all().order_by('-created_at')
    serializer_class = serializers.BinProductSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['bin__client', 'bin__status']
    search_fields = ['entry_product__product__name',
                     'entry_product__product__client_sku']
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]

    client_field = 'bin__client'

    def get_queryset(self):
        return self.scope_to_user(super().get_queryset())

    def perform_create(self, serializer):
        user = self.request.user
        bin = serializer.validated_data.get('bin')
        if is_client_user(user):
            if bin and bin.client_id != get_user_client_id(user):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied(
                    'No puede modificar bins de otro cliente')
        serializer.save()

    @transaction.atomic
    def perform_update(self, serializer):
        return self.perform_create(serializer)

    @action(detail=False, methods=['get'], url_path='consolidated')
    def consolidated_products(self, request):
        """
        Retorna una lista consolidada de productos, sumando las cantidades
        por ID del producto y restando las cantidades que están en órdenes 
        de salida pendientes o en progreso.
        """
        from django.db.models import Sum, F
        from apps.order.models import ExitProductGroup, OrderStatus

        # Aplicar filtros a la consulta base y filtrar por bins activos y almacenados
        queryset = self.filter_queryset(self.get_queryset()).filter(
            bin__is_active=True,  # Solo bins activos (Bin.is_active)
            bin__status=choices.BinStatus.ALMACENADO  # Solo bins almacenados
        )

        # Agrupar por producto y sumar cantidades
        consolidated = queryset.values(
            'entry_product__product__id',
        ).annotate(
            total_quantity=Sum('quantity'),
            product_name=F('entry_product__product__name'),
            product_id=F('entry_product__product__id'),
            uom=F('entry_product__product__uom'),
            client_sku=F('entry_product__product__client_sku')
        ).order_by('product_name')

        # Obtener el cliente del filtro para filtrar las reservas por el mismo cliente
        client_id = request.query_params.get('bin__client')
        if not client_id:
            # Si no hay filtro de cliente, intentar obtenerlo del scope del usuario
            user = request.user
            if is_client_user(user):
                client_id = get_user_client_id(user)

        # Obtener el ID de la salida a excluir (si se está editando una salida existente)
        exclude_exit_id = request.query_params.get('exclude_exit_id')
        if exclude_exit_id:
            try:
                exclude_exit_id = int(exclude_exit_id)
            except (ValueError, TypeError):
                exclude_exit_id = None

        # Obtener productos con sus cantidades ya asignadas en órdenes de salida pendientes
        # Solo contar ExitProducts que NO están procesados (is_processed=False)
        # IMPORTANTE: Filtrar por el mismo cliente para no restar reservas de otros clientes
        # IMPORTANTE: Excluir la salida actual si se está editando para no restar sus propias reservas
        pending_quantities = {}
        for item in consolidated:
            product_id = item['product_id']

            # Obtener cantidades en órdenes de salida pendientes o en progreso
            # Solo contar los ExitProducts que aún no han sido procesados
            # Y que pertenecen al mismo cliente
            from apps.order.models import ExitProduct
            exit_query = ExitProduct.objects.filter(
                exit_product_group__product_id=product_id,
                exit_product_group__exit__status__in=[OrderStatus.PENDIENTE,
                                                      OrderStatus.EN_PROGRESO],
                is_processed=False  # Solo los no procesados
            )

            # Filtrar por cliente si se especificó
            if client_id:
                exit_query = exit_query.filter(
                    exit_product_group__exit__client_id=client_id
                )

            # Excluir la salida actual si se está editando
            if exclude_exit_id:
                exit_query = exit_query.exclude(
                    exit_product_group__exit_id=exclude_exit_id
                )

            exit_quantities = exit_query.aggregate(
                total=Sum('quantity'))['total'] or 0

            # Guardar en diccionario para usar después
            pending_quantities[product_id] = exit_quantities

        # Simplificar la respuesta y restar cantidades pendientes
        simplified_result = []
        for item in consolidated:
            product_id = item['product_id']
            total_quantity = item['total_quantity']
            pending_quantity = pending_quantities.get(product_id, 0)

            # Calcular la cantidad disponible real
            available_quantity = max(0, total_quantity - pending_quantity)

            # Determinar si está completamente reservado
            is_fully_reserved = total_quantity > 0 and available_quantity == 0

            # Incluir productos con cantidad disponible O completamente reservados (para mostrar aviso)
            if available_quantity > 0 or is_fully_reserved:
                simplified_result.append({
                    'product_id': product_id,
                    'name': item['product_name'],
                    'uom': item['uom'],
                    'client_sku': item.get('client_sku', ''),
                    'quantity': available_quantity,
                    'total_inventory': total_quantity,
                    'pending_in_exits': pending_quantity,
                    'is_fully_reserved': is_fully_reserved,
                    # Información de debug (puede ser útil para diagnosticar problemas)
                    'debug_info': {
                        'client_id_filter': client_id,
                        'total_quantity_calculated': total_quantity,
                        'pending_quantity_calculated': pending_quantity,
                        'available_quantity_calculated': available_quantity
                    } if request.query_params.get('debug') == 'true' else None
                })

        # Paginar resultados si es necesario
        page = self.paginate_queryset(simplified_result)
        if page is not None:
            return self.get_paginated_response(page)

        return Response(simplified_result)
