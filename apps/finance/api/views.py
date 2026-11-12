from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import NotFound, ValidationError
from apps.common.pagination import StandardPagination
from apps.common.utils.permissions_mixins import (
    StrictDjangoModelPermissions,
    DenyClientUsers,
    WorkspaceScopeMixin,
)
from django_filters.rest_framework import DjangoFilterBackend
from apps.finance import models as finance_models
from apps.finance import services as finance_services
from apps.core.models import Client
from apps.workspaces.utils.tenant import get_workspace_for_request
from datetime import datetime
from . import serializers


class TariffViewSet(WorkspaceScopeMixin, viewsets.ModelViewSet):
    queryset = finance_models.Tariff.objects.all().order_by('-created_at')
    serializer_class = serializers.TariffSerializer
    permission_classes = [IsAuthenticated,
                          DenyClientUsers, StrictDjangoModelPermissions]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name']
    filterset_fields = ['has_picking_tariff']
    ordering_fields = ['name', 'created_at', 'updated_at']
    ordering = ['-created_at']
    workspace_field = 'workspace'

    def get_queryset(self):
        return self.scope_to_workspace(super().get_queryset())

    def perform_create(self, serializer):
        from apps.audit.services.crud_hooks import SECTION, log_crud

        ws = get_workspace_for_request(self.request)
        if ws is None:
            raise ValidationError({'detail': 'Owner no resuelto.'})
        obj = serializer.save(workspace=ws)
        log_crud(
            workspace=ws,
            section=SECTION.TARIFF,
            action="create",
            verb="creó la tarifa",
            label=obj.name,
            actor=self.request.user,
            resource_id=obj.pk,
            resource_code=obj.name,
        )

    def perform_update(self, serializer):
        from apps.audit.services.crud_hooks import SECTION, log_crud

        obj = serializer.save()
        log_crud(
            workspace=obj.workspace,
            section=SECTION.TARIFF,
            action="update",
            verb="actualizó la tarifa",
            label=obj.name,
            actor=self.request.user,
            resource_id=obj.pk,
            resource_code=obj.name,
        )

    def perform_destroy(self, instance):
        from apps.audit.services.crud_hooks import SECTION, log_crud

        ws = instance.workspace
        name = instance.name
        pk = instance.pk
        instance.delete()
        log_crud(
            workspace=ws,
            section=SECTION.TARIFF,
            action="delete",
            verb="eliminó la tarifa",
            label=name,
            actor=self.request.user,
            resource_id=pk,
            resource_code=name,
        )


class BillingCalculationViewSet(viewsets.ViewSet):
    """
    ViewSet for billing calculations.
    Provides endpoints for:
    - General billing summary (all clients)
    - Client billing calculation (specific client)
    - Paginated client list with billing summary
    - Daily inventory average (integrated from averages)
    """
    permission_classes = [IsAuthenticated, DenyClientUsers]
    pagination_class = StandardPagination

    def _require_workspace(self, request):
        ws = get_workspace_for_request(request)
        if ws is None:
            raise ValidationError({'detail': 'Owner no resuelto.'})
        return ws

    def _get_client_in_workspace(self, request, pk):
        ws = self._require_workspace(request)
        try:
            return Client.objects.select_related('workspace').get(id=pk, workspace=ws)
        except Client.DoesNotExist:
            raise NotFound({'error': 'Cliente no encontrado'})

    def _get_date_range_from_params(self, request):
        """
        Obtiene rango de fechas de los parámetros.
        Si se proporcionan start_date y end_date, los usa.
        Si no, usa las fechas por defecto (27 mes anterior al 26 mes actual).
        
        IMPORTANTE: Todas las fechas se convierten a timezone de Panamá (America/Panama)
        para garantizar consistencia independientemente de la ubicación del usuario.
        """
        from django.utils import timezone
        from zoneinfo import ZoneInfo
        
        # Usar timezone de Panamá para consistencia
        panama_tz = ZoneInfo('America/Panama')
        
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')

        if start_date_str and end_date_str:
            try:
                # Parsear fecha naive y convertir a timezone de Panamá
                start_naive = datetime.strptime(start_date_str, '%Y-%m-%d')
                end_naive = datetime.strptime(end_date_str, '%Y-%m-%d')
                
                # Hacer timezone-aware en Panamá
                start_date = start_naive.replace(hour=0, minute=0, second=0, tzinfo=panama_tz)
                end_date = end_naive.replace(hour=23, minute=59, second=59, tzinfo=panama_tz)
                
                return start_date, end_date
            except ValueError:
                pass

        # Fechas por defecto: 27 del mes anterior al 26 del mes actual
        now = timezone.now().astimezone(panama_tz)
        # 26 del mes actual
        end_date = now.replace(day=26, hour=23, minute=59, second=59, microsecond=0)
        # 27 del mes anterior
        if now.month == 1:
            start_date = now.replace(year=now.year - 1, month=12, day=27, hour=0, minute=0, second=0, microsecond=0)
        else:
            start_date = now.replace(month=now.month - 1, day=27, hour=0, minute=0, second=0, microsecond=0)

        return start_date, end_date

    @action(detail=False, methods=['get'], url_path='summary')
    def general_summary(self, request):
        """
        Get general billing summary for all clients.

        GET /api/billing/summary/?start_date=2024-11-26&end_date=2024-12-26
        """
        start_date, end_date = self._get_date_range_from_params(request)
        workspace = self._require_workspace(request)

        summary = finance_services.calculate_general_billing_summary(
            start_date, end_date, workspace=workspace)
        serializer = serializers.BillingSummarySerializer(summary)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='clients')
    def clients_list(self, request):
        """
        Get paginated list of clients with basic billing info.
        Includes comparison with previous period.

        GET /api/billing/clients/?page=1&page_size=20&start_date=2024-11-26&end_date=2024-12-26
        """
        from datetime import timedelta
        from calendar import monthrange
        
        start_date, end_date = self._get_date_range_from_params(request)
        workspace = self._require_workspace(request)

        # Calcular período anterior (un mes antes)
        def subtract_month(dt):
            """Resta un mes a una fecha"""
            if dt.month == 1:
                return dt.replace(year=dt.year - 1, month=12)
            else:
                # Verificar que el día exista en el mes anterior
                prev_month = dt.month - 1
                prev_year = dt.year
                max_day = monthrange(prev_year, prev_month)[1]
                new_day = min(dt.day, max_day)
                return dt.replace(month=prev_month, day=new_day)
        
        prev_start_date = subtract_month(start_date)
        prev_end_date = subtract_month(end_date)

        # Obtener datos del período actual
        summary = finance_services.calculate_general_billing_summary(
            start_date, end_date, workspace=workspace)
        clients_data = summary['clients_summary']

        # Obtener datos del período anterior
        prev_summary = finance_services.calculate_general_billing_summary(
            prev_start_date, prev_end_date, workspace=workspace)
        prev_clients_data = {c['client_id']: c for c in prev_summary['clients_summary']}

        # Combinar datos actuales con período anterior
        for client in clients_data:
            prev_client = prev_clients_data.get(client['client_id'], {})
            client['prev_total_billing'] = prev_client.get('total_billing', 0)
            client['prev_storage_cost'] = prev_client.get('storage_cost', 0)
            client['prev_entries_cost'] = prev_client.get('entries_cost', 0)
            client['prev_exits_cost'] = prev_client.get('exits_cost', 0)
            client['prev_cost_per_pallet'] = prev_client.get('cost_per_pallet', 0)
            client['prev_average_pallets'] = prev_client.get('average_pallets', 0)
            
            # Calcular variación porcentual del costo por pallet
            current = float(client.get('cost_per_pallet', 0))
            previous = float(client.get('prev_cost_per_pallet', 0))
            if previous > 0:
                client['variation_pct'] = ((current - previous) / previous) * 100
            else:
                client['variation_pct'] = 100 if current > 0 else 0

        # Paginate manually
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(clients_data, request)

        if page is not None:
            serializer = serializers.ClientBillingSummarySerializer(
                page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = serializers.ClientBillingSummarySerializer(
            clients_data, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='calculate')
    def calculate_client(self, request, pk=None):
        """
        Calculate detailed billing for a specific client.

        GET /api/billing/{client_id}/calculate/?start_date=2024-11-26&end_date=2024-12-26
        """
        client = self._get_client_in_workspace(request, pk)
        start_date, end_date = self._get_date_range_from_params(request)

        billing_data = finance_services.calculate_client_billing(
            pk, start_date, end_date)

        if billing_data is None:
            return Response(
                {'error': 'No se pudo calcular la facturación para este cliente'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = serializers.BillingCalculationSerializer(billing_data)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='average')
    def calculate_average(self, request, pk=None):
        """
        Calcula el promedio diario de inventario almacenado para un cliente.
        Integrado desde InventoryAverageViewSet.

        GET /api/billing/{client_id}/average/?start_date=2024-11-26&end_date=2024-12-26&storage_type=all
        """
        client = self._get_client_in_workspace(request, pk)
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')
        storage_type = request.query_params.get('storage_type', 'all')

        if not start_date_str or not end_date_str:
            return Response(
                {'error': 'Los parámetros start_date y end_date son requeridos'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'error': 'Formato de fecha inválido. Use YYYY-MM-DD'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if start_date > end_date:
            return Response(
                {'error': 'La fecha de inicio debe ser menor o igual a la fecha de fin'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if (end_date - start_date).days > 365:
            return Response(
                {'error': 'El rango máximo permitido es de 365 días'},
                status=status.HTTP_400_BAD_REQUEST
            )

        result = finance_services.calculate_daily_inventory_average(
            pk, start_date, end_date, storage_type
        )

        if result is None:
            return Response(
                {'error': 'No se pudo calcular el promedio para este cliente'},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(result)

    @action(detail=True, methods=['get'], url_path='average/export')
    def export_average_excel(self, request, pk=None):
        """
        Exporta los promedios de inventario a Excel.

        GET /api/billing/{client_id}/average/export/?start_date=2024-11-26&end_date=2024-12-26&storage_type=all
        """
        from django.http import HttpResponse

        client = self._get_client_in_workspace(request, pk)
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')
        storage_type = request.query_params.get('storage_type', 'all')

        if not start_date_str or not end_date_str:
            return Response(
                {'error': 'Parámetros start_date y end_date son requeridos'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'error': 'Formato de fecha inválido'},
                status=status.HTTP_400_BAD_REQUEST
            )

        result = finance_services.calculate_daily_inventory_average(
            pk, start_date, end_date, storage_type
        )

        if result is None:
            return Response(
                {'error': 'No se pudo generar el reporte'},
                status=status.HTTP_400_BAD_REQUEST
            )

        excel_buffer = finance_services.export_inventory_average_excel(result)

        filename = f"promedios_{client.name}_{start_date_str}_{end_date_str}.xls"
        filename = filename.replace(' ', '_')

        response = HttpResponse(
            excel_buffer.getvalue(),
            content_type='application/vnd.ms-excel'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        return response

    @action(detail=True, methods=['get'], url_path='history')
    def client_history(self, request, pk=None):
        """
        Get monthly billing history for a specific client.

        GET /api/billing/{client_id}/history/?months=12

        Params:
        - months: Number of months to retrieve (default: 12)
        """
        client = self._get_client_in_workspace(request, pk)
        months = int(request.query_params.get('months', 12))
        history_data = finance_services.calculate_client_monthly_history(
            pk, months)

        if history_data is None:
            return Response(
                {'error': 'No se pudo calcular el historial para este cliente'},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(history_data)

    @action(detail=True, methods=['get'], url_path='billing-report/export')
    def export_billing_report(self, request, pk=None):
        """
        Exporta un reporte de facturación completo a Excel con 4 pestañas:
        1. Resumen de Facturación
        2. Movimientos In
        3. Movimientos Out
        4. Promedio de Almacenamiento

        GET /api/billing/{client_id}/billing-report/export/?start_date=2024-11-27&end_date=2024-12-26
        """
        from django.http import HttpResponse

        client = self._get_client_in_workspace(request, pk)
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')

        if not start_date_str or not end_date_str:
            return Response(
                {'error': 'Parámetros start_date y end_date son requeridos'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'error': 'Formato de fecha inválido'},
                status=status.HTTP_400_BAD_REQUEST
            )

        excel_buffer = finance_services.generate_billing_report_excel(
            pk, start_date, end_date
        )

        if excel_buffer is None:
            return Response(
                {'error': 'No se pudo generar el reporte'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Formatear nombre del archivo (usa el mes de la fecha FIN)
        month_name = end_date.strftime('%B').upper()
        filename = f"Facturacion_{client.name}_{month_name}_{start_date_str}_{end_date_str}.xls"
        filename = filename.replace(' ', '_')

        response = HttpResponse(
            excel_buffer.getvalue(),
            content_type='application/vnd.ms-excel'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        return response

    @action(detail=True, methods=['get'], url_path='invoice/next-number')
    def get_next_invoice_number(self, request, pk=None):
        """
        Obtiene el próximo número de comprobante disponible.
        
        GET /api/billing/{client_id}/invoice/next-number/
        """
        client = self._get_client_in_workspace(request, pk)
        next_number = finance_models.Invoice.get_next_number(client.workspace)
        return Response({'next_number': next_number})

    @action(detail=True, methods=['post'], url_path='invoice/generate')
    def generate_invoice(self, request, pk=None):
        """
        Genera un comprobante PDF para un cliente.
        
        POST /api/billing/{client_id}/invoice/generate/
        Body: {
            "client_ruc": "1086478-1-555831",
            "client_dv": "78",
            "client_address": "Dirección del cliente",
            "vendor": "GLORIA MORENO",
            "comment": "SERVICIOS PRESTADOS EN EL MES DE ENERO DE 2026",
            "number": "" // Opcional, si está vacío se genera automáticamente
        }
        Query params: start_date, end_date
        """
        from django.http import HttpResponse
        
        client = self._get_client_in_workspace(request, pk)
        # Validar datos del request
        create_serializer = serializers.InvoiceCreateSerializer(data=request.data)
        if not create_serializer.is_valid():
            return Response(create_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = create_serializer.validated_data
        
        # Obtener fechas del período
        start_date, end_date = self._get_date_range_from_params(request)
        
        # Calcular billing para obtener los datos
        billing_data = finance_services.calculate_client_billing(
            pk, start_date, end_date)
        
        if billing_data is None:
            return Response(
                {'error': 'No se pudo calcular la facturación para este cliente'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Generar número de comprobante si no se proporcionó
        invoice_number = data.get('number', '').strip()
        if not invoice_number:
            invoice_number = finance_models.Invoice.get_next_number(client.workspace)
        
        # Verificar que el número no exista
        if finance_models.Invoice.objects.filter(number=invoice_number).exists():
            return Response(
                {'error': f'El número de comprobante {invoice_number} ya existe'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Crear el comprobante en la base de datos
        invoice = finance_models.Invoice.objects.create(
            number=invoice_number,
            client=client,
            billing_start_date=start_date.date() if hasattr(start_date, 'date') else start_date,
            billing_end_date=end_date.date() if hasattr(end_date, 'date') else end_date,
            client_ruc=data['client_ruc'],
            client_dv=data['client_dv'],
            client_address=data.get('client_address', ''),
            vendor=data.get('vendor', ''),
            comment=data.get('comment', ''),
            subtotal=billing_data['total_billing'],
            tax_exempt=billing_data['total_billing'],  # Por ahora todo es exento
            tax_amount=0,
            total=billing_data['total_billing'],
            line_items=finance_services.generate_invoice_line_items(billing_data),
            billing_data=finance_services.serialize_billing_data(billing_data),
            created_by=request.user
        )
        
        invoice = finance_models.Invoice.objects.select_related(
            'client__workspace',
        ).get(pk=invoice.pk)

        # Generar el PDF
        pdf_buffer = finance_services.generate_invoice_pdf(invoice, billing_data)
        
        if pdf_buffer is None:
            return Response(
                {'error': 'No se pudo generar el PDF del comprobante'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        # Retornar el PDF
        filename = f"Comprobante_{invoice_number}_{client.name}.pdf"
        filename = filename.replace(' ', '_')
        
        response = HttpResponse(
            pdf_buffer.getvalue(),
            content_type='application/pdf'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response

    @action(detail=True, methods=['get'], url_path='invoices')
    def list_invoices(self, request, pk=None):
        """
        Lista los comprobantes generados para un cliente.
        
        GET /api/billing/{client_id}/invoices/
        """
        client = self._get_client_in_workspace(request, pk)        
        invoices = finance_models.Invoice.objects.filter(client=client).order_by('-created_at')
        serializer = serializers.InvoiceSerializer(invoices, many=True)
        return Response(serializer.data)


