from rest_framework import serializers
from apps.finance import models as finance_models


class TariffSerializer(serializers.ModelSerializer):
    """
    Serializer for Tariff model with dynamic/static tariff fields.
    All entry/exit rates are by condition (Frozen, Refrigerated, Dry).
    """
    class Meta:
        model = finance_models.Tariff
        fields = [
            'id', 'name',
            # Entry/Exit rates by condition - FROZEN
            'in_pallet_frozen', 'in_floor_frozen', 'out_frozen',
            # Entry/Exit rates by condition - REFRIGERATED
            'in_pallet_refrigerated', 'in_floor_refrigerated', 'out_refrigerated',
            # Entry/Exit rates by condition - DRY
            'in_pallet_dry', 'in_floor_dry', 'out_dry',
            # Storage rates (monthly)
            'refrigerated_storage', 'frozen_storage', 'dry_storage',
            # Services
            'picking', 'has_picking_tariff', 'crossdocking', 'restiba', 'added_pallet', 'wrap',
            # Dynamic/Static tariff configuration
            'rotation_threshold', 'static_penalty_rate', 'monthly_storage_rate',
            # Timestamps
            'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']


class BillingCalculationSerializer(serializers.Serializer):
    """
    Serializer for client billing calculation response.
    Includes dynamic/static tariff information.
    """
    client_id = serializers.IntegerField()
    client_name = serializers.CharField()
    client_ruc = serializers.CharField(allow_blank=True, required=False)
    client_dv = serializers.CharField(allow_blank=True, required=False)
    client_address = serializers.CharField(allow_blank=True, required=False)
    tariff_name = serializers.CharField()

    # Tariff type information
    tariff_type = serializers.ChoiceField(
        choices=['dynamic', 'static'],
        help_text='Type of tariff applied: dynamic (rotation >= threshold) or static'
    )
    rotation_percentage = serializers.FloatField(
        help_text='Rotation percentage: (entries + exits) / average_pallets * 100'
    )
    rotation_threshold = serializers.FloatField(
        help_text='Minimum rotation percentage to apply dynamic tariff'
    )
    average_pallets = serializers.FloatField(
        help_text='Average number of pallets in the period'
    )
    period_days = serializers.IntegerField(
        help_text='Number of days in the billing period'
    )
    cost_per_pallet = serializers.FloatField(
        help_text='Total cost divided by average pallets'
    )

    # Cost breakdown
    storage = serializers.DictField()
    entries = serializers.DictField()
    exits = serializers.DictField()
    picking = serializers.DictField()
    additional_services = serializers.DictField()

    # Static tariff details (only present when tariff_type is 'static')
    static_details = serializers.DictField(
        required=False,
        help_text='Details of static tariff calculation (penalty rates, actual vs charged)'
    )

    total_billing = serializers.DecimalField(max_digits=12, decimal_places=2)


class BillingSummarySerializer(serializers.Serializer):
    """
    Serializer for general billing summary response.
    """
    total_clients = serializers.IntegerField()
    total_billing = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_storage_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2)
    total_entries_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2)
    total_exits_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2)
    total_picking_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2)
    total_additional_services = serializers.DecimalField(
        max_digits=12, decimal_places=2)
    total_bins = serializers.IntegerField()
    total_average_pallets = serializers.IntegerField()
    total_positions = serializers.IntegerField()
    total_entries = serializers.IntegerField()
    total_exits = serializers.IntegerField()
    total_picking = serializers.IntegerField()
    clients_summary = serializers.ListField()


class ClientBillingSummarySerializer(serializers.Serializer):
    """
    Serializer for paginated client list with basic billing info.
    Includes previous period comparison.
    """
    client_id = serializers.IntegerField()
    client_name = serializers.CharField()
    total_billing = serializers.DecimalField(max_digits=12, decimal_places=2)
    storage_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    bins_count = serializers.IntegerField()
    entries_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    entries_count = serializers.IntegerField()
    exits_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    exits_count = serializers.IntegerField()
    picking_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    picking_count = serializers.IntegerField()
    # Tariff type fields
    tariff_type = serializers.CharField(required=False)
    rotation_percentage = serializers.FloatField(required=False)
    average_pallets = serializers.FloatField(required=False, default=0)
    cost_per_pallet = serializers.FloatField(required=False, default=0)
    # Previous period comparison fields
    prev_total_billing = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=0)
    prev_storage_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=0)
    prev_entries_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=0)
    prev_exits_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=0)
    prev_cost_per_pallet = serializers.FloatField(required=False, default=0)
    prev_average_pallets = serializers.FloatField(required=False, default=0)
    variation_pct = serializers.FloatField(required=False, default=0)


class InvoiceSerializer(serializers.ModelSerializer):
    """
    Serializer for Invoice (comprobante) model.
    """
    client_name = serializers.CharField(source='client.name', read_only=True)
    next_number = serializers.SerializerMethodField()
    
    class Meta:
        model = finance_models.Invoice
        fields = [
            'id', 'number', 'client', 'client_name',
            'billing_start_date', 'billing_end_date',
            'client_ruc', 'client_dv', 'client_address',
            'billing_point', 'vendor', 'comment',
            'subtotal', 'tax_exempt', 'tax_amount', 'total',
            'line_items', 'billing_data',
            'created_at', 'created_by', 'next_number'
        ]
        read_only_fields = ['created_at', 'created_by', 'next_number']
    
    def get_next_number(self, obj):
        """Retorna el próximo número de comprobante disponible."""
        return finance_models.Invoice.get_next_number()


class InvoiceCreateSerializer(serializers.Serializer):
    """
    Serializer para crear un comprobante desde la vista de billing.
    """
    # Datos fiscales del cliente
    client_ruc = serializers.CharField(max_length=50, help_text='RUC/Cédula del cliente')
    client_dv = serializers.CharField(max_length=10, help_text='Dígito verificador')
    client_address = serializers.CharField(required=False, allow_blank=True, help_text='Dirección fiscal')
    
    # Datos adicionales
    vendor = serializers.CharField(required=False, allow_blank=True, max_length=100, help_text='Vendedor')
    comment = serializers.CharField(required=False, allow_blank=True, help_text='Comentario adicional')
    
    # El número se genera automáticamente si no se proporciona
    number = serializers.CharField(required=False, allow_blank=True, max_length=20, help_text='Número de comprobante (opcional, se genera automáticamente)')
