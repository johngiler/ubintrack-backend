from rest_framework import mixins, viewsets, filters
from apps.core.utils.user_roles import get_user_client, get_user_client_id, is_client_user, can_manage_as_administrator
from rest_framework.permissions import IsAuthenticated, DjangoModelPermissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError as DRFValidationError
from django.core.exceptions import ValidationError as DjangoValidationError
from django_filters.rest_framework import DjangoFilterBackend, FilterSet, CharFilter, DateFilter
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from datetime import datetime

from apps.order import models as order_models
from apps.order.services import export_entry_excel, export_exit_excel
from apps.inventory import models as inventory_models
from apps.core.choices import BinStatus
from apps.common.utils.permissions_mixins import ClientScopeMixin, StrictDjangoModelPermissions
from . import serializers


class EntryFilter(FilterSet):
    status = CharFilter(method='filter_status')

    def filter_status(self, queryset, name, value):
        if value:
            status_list = value.split(',')
            status_q = Q()
            for status in status_list:
                status_q |= Q(status=status.strip())
            return queryset.filter(status_q)
        return queryset

    class Meta:
        model = order_models.Entry
        fields = ['status', 'condition']


class EntryViewSet(ClientScopeMixin, viewsets.ModelViewSet):
    queryset = order_models.Entry.objects.all().order_by('-created_at')
    serializer_class = serializers.EntrySerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_class = EntryFilter
    # Buscar por código de entrada, cliente o SKU del producto
    search_fields = ['code', 'client__name',
                     'entry_products__product__client_sku']
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]

    def get_queryset(self):
        return self.scope_to_user(super().get_queryset()).distinct()

    def filter_queryset(self, queryset):
        # Verificar si es búsqueda por bin antes de aplicar filtros
        search = self.request.query_params.get('search', '')
        if search and search.upper().startswith('BIN-'):
            # Si busca un código de bin, filtrar por bins relacionados (sin usar SearchFilter)
            queryset = queryset.filter(
                entry_products__bin_products__bin__code__icontains=search
            ).distinct()
            # Aplicar solo DjangoFilterBackend, no SearchFilter
            for backend in self.filter_backends:
                if backend != filters.SearchFilter:
                    queryset = backend().filter_queryset(self.request, queryset, self)
            return queryset
        # Para otras búsquedas, usar el comportamiento normal
        return super().filter_queryset(queryset)

    def perform_create(self, serializer):
        user = self.request.user
        client = None
        if is_client_user(user):
            client = get_user_client(user)
            # Limitar estados permitidos para usuarios cliente
            status = serializer.validated_data.get('status')
            if status not in (None, 'pendiente', 'cancelada'):
                from rest_framework.exceptions import ValidationError
                raise ValidationError(
                    {'status': 'Usuarios cliente solo pueden establecer estados "pendiente" o "cancelada".'})
        serializer.save(
            client=client or serializer.validated_data.get('client'))

    def perform_update(self, serializer):
        from rest_framework.exceptions import ValidationError

        instance = self.get_object()
        old_status = instance.status
        new_status = serializer.validated_data.get('status', instance.status)

        # Campos permitidos para editar en entradas completadas
        campos_servicios = {'total_restibas',
                            'total_wraps', 'total_added_pallets'}

        # VALIDACIÓN: Si está completada, solo se pueden modificar campos de servicios
        if old_status == 'completada':
            campos_modificados = set(serializer.validated_data.keys())
            campos_no_permitidos = campos_modificados - campos_servicios
            if campos_no_permitidos:
                raise ValidationError({
                    'status': 'Solo se pueden modificar los campos de servicios (restibas, wraps, pallets) en una entrada completada.'
                })

        # VALIDACIÓN: No se puede cancelar si tiene bins asociados
        if new_status == 'cancelada' and old_status != 'cancelada':
            has_bins = instance.bins.exists()
            if has_bins:
                raise ValidationError({
                    'status': 'No se puede cancelar la entrada porque tiene bins asociados. Primero elimine los bins.'
                })

        user = self.request.user
        if is_client_user(user):
            # Validar y restringir estados
            if 'status' in serializer.validated_data and new_status not in ('pendiente', 'cancelada'):
                raise ValidationError(
                    {'status': 'Usuarios cliente solo pueden establecer estados "pendiente" o "cancelada".'})
            serializer.save(client=get_user_client(user))
        else:
            serializer.save()

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError

        # VALIDACIÓN: No se puede eliminar una entrada completada
        if instance.status == 'completada':
            raise ValidationError(
                'No se puede eliminar una entrada completada.'
            )

        # VALIDACIÓN: No se puede eliminar si tiene bins asociados
        has_bins = instance.bins.exists()
        if has_bins:
            raise ValidationError(
                'No se puede eliminar la entrada porque tiene bins asociados. Primero elimine los bins.'
            )

        # Si pasa las validaciones, eliminar
        instance.delete()

    @action(detail=False, methods=['get'])
    def export_excel(self, request):
        """
        Export entry orders to Excel file
        """
        queryset = self.get_queryset()

        # Apply filters if any
        queryset = self.filter_queryset(queryset)

        # Prefetch related data to optimize queries
        queryset = queryset.select_related(
            'client').prefetch_related('entry_products__product')

        # Generate Excel file
        output = export_entry_excel(queryset)

        # Prepare response
        filename = f"Ordenes_Entrada_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xls"
        response = HttpResponse(
            output.getvalue(),
            content_type='application/vnd.ms-excel'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        return response

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """
        Completar una entrada con los servicios adicionales.

        Para entradas a PISO:
        - restiba y wrap se auto-calculan (= total bins)
        - pallet_adicional es editable

        Para entradas PALETIZADAS:
        - restiba es editable (puede ser 0)
        - wrap = restiba
        - pallet_adicional es editable
        """
        from rest_framework.exceptions import ValidationError
        from apps.core.choices import InTypes

        entry = self.get_object()

        # Validar que no esté ya completada
        if entry.status == 'completada':
            raise ValidationError({'detail': 'La entrada ya está completada.'})

        # Validar que tenga bins
        bins_count = entry.bins.count()
        if bins_count == 0:
            raise ValidationError(
                {'detail': 'La entrada no tiene bins asociados.'})

        # Obtener datos del request
        data = request.data
        is_piso = entry.in_type == InTypes.PISO

        if is_piso:
            # Para PISO: restiba y wrap son iguales al total de bins (auto)
            entry.total_restibas = bins_count
            entry.total_wraps = bins_count
            # Pallet adicional viene del request o default a bins_count
            entry.total_added_pallets = int(
                data.get('total_added_pallets', bins_count))
        else:
            # Para PALETIZADO: restiba es editable, wrap = restiba
            restiba = int(data.get('total_restibas', 0))
            entry.total_restibas = restiba
            entry.total_wraps = restiba  # wrap = restiba
            # Pallet adicional viene del request o default a restiba
            entry.total_added_pallets = int(
                data.get('total_added_pallets', restiba))

        # Completar la entrada
        entry.status = 'completada'
        entry.save()

        # Generar PDF de forma síncrona
        from apps.order.services import update_entry_pdf
        from apps.order.models import EntryDetail

        pdf_url = None
        try:
            # Recargar la entrada para tener datos actualizados
            entry.refresh_from_db()

            # Crear o obtener el detalle
            try:
                entry_detail = entry.entry_detail
            except EntryDetail.DoesNotExist:
                entry_detail = EntryDetail.objects.create(entry=entry)

            # Generar el PDF
            pdf_path = update_entry_pdf(entry)
            if pdf_path:
                entry_detail.pdf_document = pdf_path
                entry_detail.save(update_fields=['pdf_document'])
                pdf_url = pdf_path
        except Exception as e:
            print(f"Error al generar PDF para entrada {entry.code}: {e}")

        return Response({
            'detail': 'Entrada completada exitosamente.',
            'entry': {
                'id': entry.id,
                'code': entry.code,
                'status': entry.status,
                'in_type': entry.in_type,
                'bins_count': bins_count,
                'total_restibas': entry.total_restibas,
                'total_wraps': entry.total_wraps,
                'total_added_pallets': entry.total_added_pallets,
                'pdf_url': pdf_url
            }
        })

    @action(detail=True, methods=['post'])
    def regenerate_pdf(self, request, pk=None):
        """
        Regenerar el PDF de una entrada de forma síncrona.
        """
        from apps.order.services import update_entry_pdf
        from apps.order.models import EntryDetail

        entry = self.get_object()

        try:
            entry.refresh_from_db()

            try:
                entry_detail = entry.entry_detail
            except EntryDetail.DoesNotExist:
                entry_detail = EntryDetail.objects.create(entry=entry)

            pdf_path = update_entry_pdf(entry)
            if pdf_path:
                entry_detail.pdf_document = pdf_path
                entry_detail.save(update_fields=['pdf_document'])
                return Response({
                    'detail': 'PDF regenerado exitosamente.',
                    'pdf_url': pdf_path
                })
            else:
                return Response(
                    {'detail': 'No se pudo generar el PDF.'},
                    status=400
                )
        except Exception as e:
            return Response(
                {'detail': f'Error al regenerar PDF: {str(e)}'},
                status=500
            )


class EntryProductViewSet(ClientScopeMixin, viewsets.ModelViewSet):
    queryset = order_models.EntryProduct.objects.all().order_by('-created_at')
    serializer_class = serializers.EntryProductSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['entry', 'is_completed']
    search_fields = ['product__name',]
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]

    client_field = 'entry__client'

    def get_queryset(self):
        return self.scope_to_user(super().get_queryset())

    def perform_create(self, serializer):
        from rest_framework.exceptions import ValidationError
        
        user = self.request.user
        entry = serializer.validated_data.get('entry')
        
        # VALIDACIÓN: No se puede crear productos en entrada completada
        if entry and entry.status == 'completada':
            raise ValidationError(
                'No se puede agregar productos a una entrada completada.'
            )
        
        if is_client_user(user):
            # Validar que la entrada pertenezca al cliente del usuario
            if entry and entry.client_id != get_user_client_id(user):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied(
                    'No puede modificar entradas de otro cliente')
        serializer.save()

    def perform_update(self, serializer):
        user = self.request.user
        entry = serializer.validated_data.get('entry', serializer.instance.entry)
        if is_client_user(user):
            if entry and entry.client_id != get_user_client_id(user):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied(
                    'No puede modificar entradas de otro cliente')
        serializer.save()

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError

        # VALIDACIÓN: No se puede eliminar si la entrada está completada
        if instance.entry.status == 'completada':
            raise ValidationError(
                'No se puede eliminar un producto de una entrada completada.'
            )

        # VALIDACIÓN: No se puede eliminar si tiene bins asociados
        if instance.bin_products.exists():
            raise ValidationError(
                'No se puede eliminar este producto porque tiene bins asociados. '
                'Primero elimine los bins que contienen este producto.'
            )

        instance.delete()


class EntryDetailViewSet(ClientScopeMixin, mixins.CreateModelMixin,
                         mixins.UpdateModelMixin,
                         mixins.DestroyModelMixin,
                         viewsets.GenericViewSet):
    queryset = order_models.EntryDetail.objects.all().order_by('-created_at')
    serializer_class = serializers.EntryDetailSerializer
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]

    client_field = 'entry__client'

    def get_queryset(self):
        return self.scope_to_user(super().get_queryset())

    def perform_create(self, serializer):
        user = self.request.user
        entry = serializer.validated_data.get('entry')
        if is_client_user(user):
            if entry and entry.client_id != get_user_client_id(user):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied(
                    'No puede modificar entradas de otro cliente')
        serializer.save()

    def perform_update(self, serializer):
        return self.perform_create(serializer)


class ExitFilter(FilterSet):
    status = CharFilter(method='filter_status')
    exit_date = DateFilter(field_name='exit_date')

    def filter_status(self, queryset, name, value):
        if value:
            status_list = value.split(',')
            status_q = Q()
            for status in status_list:
                status_q |= Q(status=status.strip())
            return queryset.filter(status_q)
        return queryset

    class Meta:
        model = order_models.Exit
        fields = ['status', 'exit_date']


class ExitViewSet(ClientScopeMixin, viewsets.ModelViewSet):
    queryset = order_models.Exit.objects.all().order_by('-created_at')
    serializer_class = serializers.ExitSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_class = ExitFilter
    # Buscar por código de salida, cliente o SKU del producto
    search_fields = ['code', 'client__name',
                     'exit_product_groups__product__client_sku']
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]

    def get_queryset(self):
        return self.scope_to_user(super().get_queryset()).distinct()

    def filter_queryset(self, queryset):
        # Verificar si es búsqueda por bin antes de aplicar filtros
        search = self.request.query_params.get('search', '')
        if search and search.upper().startswith('BIN-'):
            # Si busca un código de bin, filtrar por bins relacionados (sin usar SearchFilter)
            queryset = queryset.filter(
                exit_product_groups__exit_products__bin_product__bin__code__icontains=search
            ).distinct()
            # Aplicar solo DjangoFilterBackend, no SearchFilter
            for backend in self.filter_backends:
                if backend != filters.SearchFilter:
                    queryset = backend().filter_queryset(self.request, queryset, self)
            return queryset

        # Verificar si es búsqueda por SKU (no contiene espacios y no parece código de salida)
        if search and not ' ' in search and not search.upper().startswith('SALIDA'):
            # Intentar filtrar por SKU primero
            sku_results = queryset.filter(
                exit_product_groups__product__client_sku__icontains=search
            ).distinct()
            if sku_results.exists():
                # Si hay resultados por SKU, usar esos
                for backend in self.filter_backends:
                    if backend != filters.SearchFilter:
                        sku_results = backend().filter_queryset(self.request, sku_results, self)
                return sku_results

        # Para otras búsquedas, usar el comportamiento normal
        return super().filter_queryset(queryset)

    def perform_create(self, serializer):
        user = self.request.user
        client = None
        if is_client_user(user):
            client = get_user_client(user)
            # Restringir estados permitidos para usuarios cliente
            status = serializer.validated_data.get('status')
            if status not in (None, 'pendiente', 'cancelada'):
                from rest_framework.exceptions import ValidationError
                raise ValidationError(
                    {'status': 'Usuarios cliente solo pueden establecer estados "pendiente" o "cancelada".'})
        serializer.save(
            client=client or serializer.validated_data.get('client'))

    # Eliminar la primera versión duplicada de perform_update y consolidar lógica aquí
    def perform_update(self, serializer):
        from rest_framework.exceptions import ValidationError

        # Obtener la instancia antes de la actualización
        instance = self.get_object()
        old_status = instance.status

        # Estado candidato
        new_status = serializer.validated_data.get('status', instance.status)

        # VALIDACIÓN: No se puede modificar una salida completada
        if old_status == 'completada':
            raise ValidationError({
                'status': 'No se puede modificar una salida completada.'
            })

        # VALIDACIÓN: No se puede cancelar si tiene bins procesados
        if new_status == 'cancelada' and old_status != 'cancelada':
            has_processed_bins = any(
                ep.is_processed
                for group in instance.exit_product_groups.all()
                for ep in group.exit_products.all()
            )
            if has_processed_bins:
                raise ValidationError({
                    'status': 'No se puede cancelar la salida porque tiene bins procesados. Primero reverse los bins procesados.'
                })

        user = self.request.user
        if is_client_user(user):
            # Limitar estados para usuarios cliente
            if 'status' in serializer.validated_data and new_status not in ('pendiente', 'cancelada'):
                raise ValidationError(
                    {'status': 'Usuarios cliente solo pueden establecer estados "pendiente" o "cancelada".'})
            # Forzar client al del usuario
            serializer.validated_data['client'] = get_user_client(user)

        # Validación para cambio a COMPLETADA
        if (old_status != 'completada' and new_status == 'completada'):
            # Verificar que todos los exit_products estén procesados
            all_exit_products = []
            for group in instance.exit_product_groups.all():
                all_exit_products.extend(group.exit_products.all())

            if all_exit_products:
                all_processed = all(
                    ep.is_processed for ep in all_exit_products)
                if not all_processed:
                    raise ValidationError({
                        'status': 'No se puede completar la orden. Todos los productos deben estar procesados (is_processed=True).'
                    })

        # Guardar
        updated_instance = serializer.save()

        # Si cambió a COMPLETADA, ejecutar lógica de despacho de bins
        if (old_status != 'completada' and updated_instance.status == 'completada'):
            updated_instance.update_bins_to_dispatched_if_all_processed()

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError

        # VALIDACIÓN: No se puede eliminar una salida completada
        if instance.status == 'completada':
            raise ValidationError(
                'No se puede eliminar una salida completada.'
            )

        # VALIDACIÓN: No se puede eliminar si tiene bins procesados
        has_processed_bins = any(
            ep.is_processed
            for group in instance.exit_product_groups.all()
            for ep in group.exit_products.all()
        )
        if has_processed_bins:
            raise ValidationError(
                'No se puede eliminar la salida porque tiene bins procesados. Primero reverse los bins procesados.'
            )

        # Si pasa las validaciones, eliminar
        instance.delete()

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """
        Completar una salida y generar el PDF de forma síncrona.
        """
        from rest_framework.exceptions import ValidationError
        from apps.order.services import update_exit_pdf
        from apps.order.models import ExitDetail

        exit_obj = self.get_object()

        # Validar que no esté ya completada
        if exit_obj.status == 'completada':
            raise ValidationError({'detail': 'La salida ya está completada.'})

        # Verificar que todos los exit_products estén procesados
        all_exit_products = []
        for group in exit_obj.exit_product_groups.all():
            all_exit_products.extend(group.exit_products.all())

        if all_exit_products:
            all_processed = all(ep.is_processed for ep in all_exit_products)
            if not all_processed:
                raise ValidationError({
                    'detail': 'No se puede completar la orden. Todos los productos deben estar procesados.'
                })

        # Completar la salida
        exit_obj.status = 'completada'
        exit_obj.save()

        # Ejecutar lógica de despacho de bins
        exit_obj.update_bins_to_dispatched_if_all_processed()

        # Generar PDF de forma síncrona
        pdf_url = None
        try:
            exit_obj.refresh_from_db()

            try:
                exit_detail = exit_obj.exit_detail
            except ExitDetail.DoesNotExist:
                exit_detail = ExitDetail.objects.create(exit=exit_obj)

            pdf_path = update_exit_pdf(exit_obj)
            if pdf_path:
                exit_detail.pdf_document = pdf_path
                exit_detail.save(update_fields=['pdf_document'])
                pdf_url = pdf_path
        except Exception as e:
            print(f"Error al generar PDF para salida {exit_obj.code}: {e}")

        return Response({
            'detail': 'Salida completada exitosamente.',
            'exit': {
                'id': exit_obj.id,
                'code': exit_obj.code,
                'status': exit_obj.status,
                'pdf_url': pdf_url
            }
        })

    @action(detail=False, methods=['get'])
    def export_excel(self, request):
        """
        Export exit orders to Excel file
        """
        queryset = self.get_queryset()

        # Apply filters if any
        queryset = self.filter_queryset(queryset)

        # Prefetch related data to optimize queries
        queryset = queryset.select_related('client', 'branch').prefetch_related(
            'exit_product_groups__product',
            'exit_product_groups__exit_products__bin_product__bin',
            'exit_product_groups__exit_products__bin_product__entry_product'
        )

        # Generate Excel file
        output = export_exit_excel(queryset)

        # Prepare response
        filename = f"Ordenes_Salida_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xls"
        response = HttpResponse(
            output.getvalue(),
            content_type='application/vnd.ms-excel'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        return response

    @action(detail=False, methods=['post'], url_path='validate-import')
    def validate_import(self, request):
        """
        Valida un archivo Excel para importación de órdenes de salida.
        Retorna errores de validación o resumen de órdenes a crear.
        """
        from apps.order.services import validate_exit_import
        from rest_framework.response import Response
        from rest_framework import status

        if 'file' not in request.FILES:
            return Response(
                {'error': 'No se proporcionó ningún archivo'},
                status=status.HTTP_400_BAD_REQUEST
            )

        file = request.FILES['file']

        # Validar extensión (.xlsx recomendado, .xls con advertencia)
        if not file.name.endswith(('.xlsx', '.xls')):
            return Response(
                {'error': 'El archivo debe ser un Excel (.xlsx)'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validar el contenido
        result = validate_exit_import(file, user=request.user)

        return Response(result)

    @action(detail=False, methods=['post'], url_path='import')
    def import_exits(self, request):
        """
        Importa órdenes de salida desde un archivo Excel.
        Primero valida y luego crea las órdenes si todo está correcto.
        """
        from apps.order.services import import_exits_from_excel
        from rest_framework.response import Response
        from rest_framework import status
        from datetime import datetime

        if 'file' not in request.FILES:
            return Response(
                {'error': 'No se proporcionó ningún archivo'},
                status=status.HTTP_400_BAD_REQUEST
            )

        file = request.FILES['file']

        # Validar extensión (.xlsx recomendado)
        if not file.name.endswith(('.xlsx', '.xls')):
            return Response(
                {'error': 'El archivo debe ser un Excel (.xlsx)'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Obtener fecha de salida override si se proporciona
        exit_date_override = None
        exit_date_str = request.data.get('exit_date')
        if exit_date_str:
            try:
                exit_date_override = datetime.strptime(
                    exit_date_str, '%Y-%m-%d').date()
            except ValueError:
                return Response(
                    {'error': 'Formato de fecha inválido. Use YYYY-MM-DD'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        try:
            result = import_exits_from_excel(
                file,
                user=request.user,
                exit_date_override=exit_date_override
            )

            if result['success']:
                return Response(result, status=status.HTTP_201_CREATED)
            else:
                return Response(result, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            return Response(
                {'error': f'Error al importar: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'], url_path='movements')
    def movements(self, request, pk=None):
        """
        Obtiene el historial de movimientos de una orden de salida específica.
        Retorna una línea de tiempo con todos los eventos: creación, cambios de estado,
        asignaciones de bins, procesamiento, reversiones, reemplazos, etc.
        """
        from rest_framework.response import Response

        exit_instance = self.get_object()

        # Obtener todos los movimientos de la salida ordenados por fecha (más recientes primero)
        movements = order_models.ExitMovement.objects.filter(
            exit=exit_instance
        ).select_related(
            'created_by',
            'product',
            'bin',
            'bin__position',
        ).order_by('-created_at')

        serializer = serializers.ExitMovementSerializer(movements, many=True)

        return Response({
            'exit_id': exit_instance.id,
            'exit_code': exit_instance.code,
            'movements': serializer.data,
            'total': movements.count()
        })


class ExitProductViewSet(ClientScopeMixin, viewsets.ModelViewSet):
    queryset = order_models.ExitProduct.objects.all().order_by('-created_at')
    serializer_class = serializers.ExitProductSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['exit_product_group__exit', 'is_picking']
    search_fields = ['bin_product__entry_product__product__name']
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]

    client_field = 'exit_product_group__exit__client'

    def get_queryset(self):
        return self.scope_to_user(super().get_queryset())

    def perform_create(self, serializer):
        user = self.request.user
        group = serializer.validated_data.get(
            'exit_product_group') or serializer.instance and serializer.instance.exit_product_group
        if is_client_user(user):
            if group and group.exit.client_id != get_user_client_id(user):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied(
                    'No puede modificar salidas de otro cliente')
        serializer.save()

    @transaction.atomic
    def perform_update(self, serializer):
        # Obtener el instance actual antes de actualizar
        instance = self.get_object()

        # Verificar si se está intentando marcar como procesado
        is_processing = serializer.validated_data.get('is_processed', False)

        if is_processing and not instance.is_processed:
            # Usar el servicio atómico para procesar
            from apps.order import services
            try:
                result = services.process_exit_product_atomic(
                    instance.id,
                    user=self.request.user
                )
                # El servicio ya hizo todo, solo retornar
                return
            except services.TransactionError as e:
                from rest_framework.exceptions import ValidationError
                raise ValidationError(str(e))
            except ValidationError as e:
                raise e

        # Si no es procesamiento, usar lógica normal
        return self.perform_create(serializer)

    @action(detail=True, methods=['post'], url_path='process')
    def process_exit_product(self, request, pk=None):
        """
        Endpoint específico para procesar un ExitProduct de forma atómica.
        Realiza todas las operaciones necesarias en una sola transacción.
        """
        from apps.order import services
        from rest_framework.response import Response
        from rest_framework import status

        try:
            result = services.process_exit_product_atomic(
                pk,
                user=request.user
            )
            return Response({
                'success': True,
                'message': 'Producto de salida procesado exitosamente',
                'data': result
            }, status=status.HTTP_200_OK)
        except services.TransactionError as e:
            return Response({
                'success': False,
                'message': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
        except (DjangoValidationError, DRFValidationError) as e:
            # Extraer datos del ValidationError para inconsistencias
            error_data = {
                'success': False,
                'message': str(e)
            }

            # Intentar extraer entry_id y entry_code de diferentes atributos del ValidationError
            entry_id = None
            entry_code = None

            # Opción 1: message_dict (Django ValidationError con dict)
            if hasattr(e, 'message_dict'):
                msg_dict = e.message_dict
                if 'entry_id' in msg_dict:
                    val = msg_dict['entry_id']
                    entry_id = val[0] if isinstance(val, list) else val
                if 'entry_code' in msg_dict:
                    val = msg_dict['entry_code']
                    entry_code = val[0] if isinstance(val, list) else val

            # Opción 2: error_dict
            if not entry_id and hasattr(e, 'error_dict'):
                err_dict = e.error_dict
                if 'entry_id' in err_dict:
                    entry_id = err_dict['entry_id']
                if 'entry_code' in err_dict:
                    entry_code = err_dict['entry_code']

            # Opción 3: args[0] si es un diccionario
            if not entry_id and e.args and isinstance(e.args[0], dict):
                arg_dict = e.args[0]
                entry_id = arg_dict.get('entry_id')
                entry_code = arg_dict.get('entry_code')

            # Opción 4: detail (DRF ValidationError)
            if not entry_id and hasattr(e, 'detail') and isinstance(e.detail, dict):
                entry_id = e.detail.get('entry_id')
                entry_code = e.detail.get('entry_code')

            if entry_id:
                error_data['entry_id'] = entry_id
            if entry_code:
                error_data['entry_code'] = entry_code

            return Response(error_data, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='reverse')
    def reverse_exit_product(self, request, pk=None):
        """
        Endpoint específico para reversar un ExitProduct procesado.
        Solo disponible para administradores y solo para salidas en_progreso.
        """
        from apps.order import services
        from rest_framework.response import Response
        from rest_framework import status

        # Verificar que el usuario sea admin (superuser o staff)
        user = request.user
        if not can_manage_as_administrator(user):
            return Response({
                'success': False,
                'message': 'Solo administradores pueden reversar operaciones'
            }, status=status.HTTP_403_FORBIDDEN)

        try:
            result = services.reverse_exit_product_atomic(
                pk,
                user=request.user
            )
            return Response({
                'success': True,
                'message': 'Operación reversada exitosamente',
                'data': result
            }, status=status.HTTP_200_OK)
        except services.TransactionError as e:
            return Response({
                'success': False,
                'message': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
        except (DjangoValidationError, DRFValidationError) as e:
            return Response({
                'success': False,
                'message': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='reassign')
    def reassign_exit_product(self, request, pk=None):
        """
        Endpoint para reasignar un ExitProduct a otro bin sin procesarlo.
        Útil cuando el usuario quiere reasignar manualmente antes de procesar.
        """
        from apps.order import services
        from rest_framework.response import Response
        from rest_framework import status

        try:
            result = services.reassign_exit_product(
                pk,
                user=request.user
            )
            return Response({
                'success': True,
                'message': result.get('message', 'Bin reasignado exitosamente'),
                'data': result
            }, status=status.HTTP_200_OK)
        except services.TransactionError as e:
            return Response({
                'success': False,
                'message': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
        except (DjangoValidationError, DRFValidationError) as e:
            return Response({
                'success': False,
                'message': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='replace-bin')
    def replace_exit_product_bin(self, request, pk=None):
        """
        Endpoint para reemplazar el bin de un ExitProduct por otro mediante código QR.

        El operador escanea un nuevo bin y el sistema:
        1. Valida que el nuevo bin tenga el mismo producto
        2. Valida que sea del mismo cliente
        3. Valida que tenga suficiente cantidad disponible
        4. Libera reservas del bin anterior
        5. Asigna el nuevo bin

        Body: { "new_bin_code": "BIN-0000001234" }
        """
        from apps.order import services
        from rest_framework.response import Response
        from rest_framework import status

        new_bin_code = request.data.get('new_bin_code')
        if not new_bin_code:
            return Response({
                'success': False,
                'message': 'El código del nuevo bin es requerido'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = services.replace_exit_product_bin(
                pk,
                new_bin_code=new_bin_code,
                user=request.user
            )
            return Response({
                'success': True,
                'message': result.get('message', 'Bin reemplazado exitosamente'),
                'data': result
            }, status=status.HTTP_200_OK)
        except services.TransactionError as e:
            return Response({
                'success': False,
                'message': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
        except (DjangoValidationError, DRFValidationError) as e:
            error_message = str(e)
            # Extraer mensaje limpio si viene como lista
            if hasattr(e, 'detail'):
                if isinstance(e.detail, list) and len(e.detail) > 0:
                    error_message = str(e.detail[0])
                elif isinstance(e.detail, dict):
                    error_message = str(e.detail)
            else:
                error_message = str(e.detail)
            return Response({
                'success': False,
                'message': error_message
            }, status=status.HTTP_400_BAD_REQUEST)

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError, PermissionDenied

        # Restringir eliminación a usuarios internos solamente
        user = self.request.user
        if is_client_user(user):
            raise PermissionDenied(
                'Solo usuarios administradores pueden eliminar bins de salidas.')

        # VALIDACIÓN: No se puede eliminar si la salida está completada
        if instance.exit_product_group.exit.status == 'completada':
            raise ValidationError(
                'No se puede eliminar un bin de una salida completada.')

        # Validar que el bin no esté procesado
        if instance.is_processed:
            raise ValidationError(
                'No se puede eliminar un bin que ya ha sido procesado.')

        instance.delete()


class ExitDetailViewSet(ClientScopeMixin, mixins.CreateModelMixin,
                        mixins.UpdateModelMixin,
                        mixins.DestroyModelMixin,
                        viewsets.GenericViewSet):
    queryset = order_models.ExitDetail.objects.all().order_by('-created_at')
    serializer_class = serializers.ExitDetailSerializer
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]

    client_field = 'exit__client'

    def get_queryset(self):
        return self.scope_to_user(super().get_queryset())

    def perform_create(self, serializer):
        user = self.request.user
        exit_obj = serializer.validated_data.get('exit')
        if is_client_user(user):
            if exit_obj and exit_obj.client_id != get_user_client_id(user):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied(
                    'No puede modificar salidas de otro cliente')
        serializer.save()

    def perform_update(self, serializer):
        return self.perform_create(serializer)


class ExitProductGroupViewSet(ClientScopeMixin, viewsets.ModelViewSet):
    queryset = order_models.ExitProductGroup.objects.all().order_by('-created_at')
    serializer_class = serializers.ExitProductGroupSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['exit', 'product']
    search_fields = ['product__name']
    permission_classes = [IsAuthenticated, DjangoModelPermissions]

    client_field = 'exit__client'

    def get_queryset(self):
        queryset = self.scope_to_user(super().get_queryset())

        exit_id = self.request.query_params.get('exit_id')
        if exit_id:
            queryset = queryset.filter(exit_id=exit_id)

        return queryset

    def perform_create(self, serializer):
        user = self.request.user
        exit_obj = serializer.validated_data.get('exit')
        if is_client_user(user):
            if exit_obj and exit_obj.client_id != get_user_client_id(user):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied(
                    'No puede modificar salidas de otro cliente')
        serializer.save()

    def perform_update(self, serializer):
        return self.perform_create(serializer)

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError, PermissionDenied

        # Restringir eliminación a usuarios internos solamente
        user = self.request.user
        if is_client_user(user):
            raise PermissionDenied(
                'Solo usuarios administradores pueden eliminar productos de salidas.')

        # VALIDACIÓN: No se puede eliminar si la salida está completada
        if instance.exit.status == 'completada':
            raise ValidationError(
                'No se puede eliminar un producto de una salida completada.')

        # Validar que ningún bin del grupo esté procesado
        if instance.exit_products.filter(is_processed=True).exists():
            raise ValidationError(
                'No se puede eliminar un producto que tiene bins procesados.')

        instance.delete()

    @action(detail=True, methods=['post'], url_path='process-all')
    def process_all(self, request, pk=None):
        """
        Procesa todos los ExitProducts no procesados de un ExitProductGroup.
        Útil para procesamiento masivo cuando hay muchos bins.
        """
        from apps.order import services
        from rest_framework import status
        from rest_framework.response import Response
        from rest_framework.exceptions import PermissionDenied

        # Restringir a usuarios internos solamente
        user = request.user
        if is_client_user(user):
            raise PermissionDenied(
                'Solo usuarios administradores pueden procesar bins masivamente.')

        try:
            result = services.process_exit_products_bulk(pk, user=user)
            return Response(result, status=status.HTTP_200_OK)
        except (DjangoValidationError, DRFValidationError) as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except services.TransactionError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
