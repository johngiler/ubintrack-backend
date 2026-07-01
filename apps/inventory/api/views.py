from rest_framework import viewsets, filters, status
from apps.core.user_roles import get_user_client, get_user_client_id, is_client_user, can_manage_as_administrator
from rest_framework.permissions import IsAuthenticated, DjangoModelPermissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django_filters.rest_framework import DjangoFilterBackend
from django.db import transaction
import xlrd
from django.http import HttpResponse
from datetime import datetime

from apps.inventory import models as inventory_models
from apps.inventory.services import export_bin_entries_excel
from utils.permissions_mixins import ClientScopeMixin, StrictDjangoModelPermissions
from apps.core import choices
from utils.zebra import print_to_zebra

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
        user = self.request.user
        client = None
        if is_client_user(user):
            client = get_user_client(user)
        serializer.save(
            client=client or serializer.validated_data.get('client'))

    def perform_update(self, serializer):
        user = self.request.user
        if is_client_user(user):
            instance = self.get_object()
            new_status = serializer.validated_data.get(
                'status', instance.status)
            if new_status == choices.BinStatus.ALMACENADO:
                qr_tag = self.request.data.get('qr_tag')
                qr_position = self.request.data.get('qr_position')
                if not (qr_tag and qr_position and qr_tag == instance.code and (
                        instance.position and qr_position == instance.position.code)):
                    from rest_framework.exceptions import PermissionDenied
                    raise PermissionDenied(
                        'Validación QR requerida para almacenar el bin.')

            serializer.save(client=get_user_client(user))
        else:
            serializer.save()


class BinViewSet(ClientScopeMixin, viewsets.ModelViewSet):
    queryset = inventory_models.Bin.objects.all().order_by('-created_at')
    serializer_class = serializers.BinSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['status', 'is_active']
    # Buscar por código de bin o SKU del producto
    search_fields = [
        'code', 'bin_products__entry_product__product__client_sku']
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]

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
            return queryset
        # Para otras búsquedas, usar el comportamiento normal
        return super().filter_queryset(queryset)

    def perform_create(self, serializer):
        user = self.request.user
        client = None
        if is_client_user(user):
            client = get_user_client(user)
        serializer.save(
            client=client or serializer.validated_data.get('client'))

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

            serializer.save(client=get_user_client(user))
        else:
            serializer.save()

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

        # Asegurar estado de la posición
        if bin_instance.position:
            from apps.storage.models import Position
            from django.utils import timezone
            Position.objects.filter(pk=bin_instance.position.pk).update(
                status=choices.PositionStatus.OCUPADO,
                updated_at=timezone.now()
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

        # Liberar la posición si existe
        if instance.position:
            from apps.storage.models import Position
            from django.utils import timezone
            Position.objects.filter(pk=instance.position.pk).update(
                status=choices.PositionStatus.LIBRE,
                updated_at=timezone.now()
            )
        # Luego eliminar la instancia
        instance.delete()

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
            'client').prefetch_related('bin_products__entry_product__product')

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

    @action(detail=False, methods=['post'], url_path='almacenar-masivo')
    @transaction.atomic
    def almacenar_masivo(self, request):
        """
        Almacena masivamente todos los bins en EN_RECIBO que tengan posición asignada.
        Solo disponible para administradores.
        Registra movimiento de historial para cada bin.
        """
        user = request.user

        # Solo admin puede usar esta función
        if not can_manage_as_administrator(user):
            return Response({
                'success': False,
                'message': 'Solo administradores pueden almacenar bins masivamente'
            }, status=status.HTTP_403_FORBIDDEN)

        # Obtener bins en EN_RECIBO con posición asignada
        bins_to_update = inventory_models.Bin.objects.filter(
            status=choices.BinStatus.EN_RECIBO,
            position__isnull=False,
            is_active=True
        ).select_related('position').prefetch_related('bin_products')

        # Filtrar por cliente si el usuario es cliente
        if is_client_user(user):
            bins_to_update = bins_to_update.filter(
                client=get_user_client(user))

        # Obtener lista de bins para crear movimientos
        bins_list = list(bins_to_update)
        total_bins = len(bins_list)

        if total_bins == 0:
            return Response({
                'success': True,
                'message': 'No hay bins en recibo con posición asignada para almacenar',
                'updated': 0
            })

        # Crear movimientos de historial para cada bin
        movements_to_create = []
        for bin in bins_list:
            # Calcular cantidad inicial del bin
            initial_qty = sum(
                bep.quantity or 0 for bep in bin.bin_products.all()
            )
            movements_to_create.append(
                inventory_models.BinMovement(
                    bin=bin,
                    movement_type=inventory_models.BinMovementType.STATUS_CHANGED,
                    old_status=choices.BinStatus.EN_RECIBO,
                    new_status=choices.BinStatus.ALMACENADO,
                    position=bin.position,
                    initial_quantity=initial_qty,
                    notes=f'Almacenado masivamente por {user.get_full_name() or user.username}.',
                    created_by=user,
                    updated_by=user,
                )
            )

        # Actualizar todos los bins
        updated = inventory_models.Bin.objects.filter(
            id__in=[b.id for b in bins_list]
        ).update(
            status=choices.BinStatus.ALMACENADO,
            updated_by=user
        )

        # Guardar movimientos
        inventory_models.BinMovement.objects.bulk_create(movements_to_create)

        return Response({
            'success': True,
            'message': f'{updated} bins almacenados exitosamente',
            'updated': updated
        })

    @action(detail=True, methods=['post'])
    def print(self, request, pk=None):
        # Restringir impresión para clientes
        if is_client_user(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('No autorizado para imprimir etiquetas.')
        """
        Endpoint para imprimir el ZPL de un bin de entrada
        """
        bin_instance = self.get_object()

        # Verificar si tiene código ZPL
        if not bin_instance.zpl_code:
            return Response({
                'success': False,
                'message': f'El bin de entrada {bin_instance.code} no tiene código ZPL generado'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Imprimir el ZPL
        success = print_to_zebra(bin_instance.zpl_code)

        if success:
            return Response({
                'success': True,
                'message': f'Etiqueta del bin de entrada {bin_instance.code} enviada a impresión'
            })
        else:
            return Response({
                'success': False,
                'message': 'Error al enviar a la impresora. Verifique la conexión.'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def regenerate_tag(self, request, pk=None):
        # Restringir regeneración de tag para clientes
        if is_client_user(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('No autorizado para regenerar tags.')
        """
        Endpoint para regenerar el tag y ZPL de un bin de entrada
        """
        bin_instance = self.get_object()
        success = bin_instance.generate_tag()

        if success:
            return Response({
                'success': True,
                'message': f'Tag y ZPL del bin de entrada {bin_instance.code} regenerados correctamente',
                'zpl_code': bin_instance.zpl_code
            })
        else:
            return Response({
                'success': False,
                'message': 'No se pudo regenerar el tag. Verifique que el bin tenga posición y productos.'
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='movements')
    def movements(self, request, pk=None):
        """
        Obtiene el historial de movimientos de un bin específico.
        Retorna una línea de tiempo con todos los eventos: creación, cambios de estado y salidas procesadas.
        """
        bin_instance = self.get_object()

        # Obtener todos los movimientos del bin ordenados por fecha (más recientes primero)
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

        serializer = serializers.BinMovementSerializer(movements, many=True)

        return Response({
            'bin_id': bin_instance.id,
            'bin_code': bin_instance.code,
            'movements': serializer.data,
            'total': movements.count()
        })


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
