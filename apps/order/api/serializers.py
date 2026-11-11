from rest_framework import serializers
from apps.core import models as core_models
from apps.order import models as order_models
from apps.inventory import models as inventory_models
from apps.storage import models as storage_models


class ClientSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = core_models.Client
        fields = ['id', 'name']


class BranchSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = core_models.Branch
        fields = ['id', 'name']


class ProductSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = inventory_models.Product
        fields = ['id', 'name', 'client_sku']


class EntryProductSerializer(serializers.ModelSerializer):
    product = ProductSimpleSerializer(read_only=True)
    product_id = serializers.PrimaryKeyRelatedField(
        queryset=inventory_models.Product.objects.all(),
        source='product',
        write_only=True
    )

    entry_id = serializers.PrimaryKeyRelatedField(
        queryset=order_models.Entry.objects.all(),
        source='entry',
        write_only=True
    )

    class Meta:
        model = order_models.EntryProduct
        fields = [
            'id',
            'quantity',
            'real_quantity',
            'production_date',
            'expiration_date',
            'batch',
            'product',
            'product_id',
            'entry_id',
            'is_completed',
            'in_inventory',
        ]


class EntryProductSimpleSerializer(serializers.ModelSerializer):
    is_completed = serializers.BooleanField(read_only=True)
    in_inventory = serializers.IntegerField(read_only=True)
    related_bins = serializers.SerializerMethodField()

    class Meta:
        model = order_models.EntryProduct
        fields = [
            'id',
            'quantity',
            'real_quantity',
            'production_date',
            'expiration_date',
            'batch',
            'is_completed',
            'in_inventory',
            'related_bins',
        ]

    def get_related_bins(self, obj):
        """Obtiene los bins relacionados a este producto de entrada"""
        from apps.inventory.models import BinProduct
        bin_products = BinProduct.objects.filter(
            entry_product=obj
        ).select_related('bin').distinct()

        # Obtener el UOM del producto (es un CharField, no una relación)
        uom_name = obj.product.uom if obj.product and obj.product.uom else 'Uds'

        bins = []
        for bep in bin_products:
            bin = bep.bin
            bins.append({
                'bin_id': bin.id,
                'bin_code': bin.code,
                'bin_status': bin.status,
                'quantity': bep.quantity,
                'uom': uom_name,
            })
        return bins

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['product'] = ProductSimpleSerializer(
            instance.product).data
        return representation


class EntryDetailSerializer(serializers.ModelSerializer):
    entry_id = serializers.PrimaryKeyRelatedField(
        queryset=order_models.Entry.objects.all(),
        source='entry',
        write_only=True
    )

    class Meta:
        model = order_models.EntryDetail
        exclude = ('created_by', 'updated_by')
        extra_kwargs = {'entry': {'read_only': True}}


class EntryDetailSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = order_models.EntryDetail
        fields = ['id', 'arrival_temperature',
                  'description', 'image', 'pdf_document']


class StorageSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = storage_models.Storage
        fields = ['id', 'name']


class EntrySerializer(serializers.ModelSerializer):
    client = ClientSimpleSerializer(read_only=True)
    storage = StorageSimpleSerializer(read_only=True)
    entry_products = EntryProductSimpleSerializer(many=True, read_only=True)
    entry_detail = EntryDetailSimpleSerializer(read_only=True)
    bins_count = serializers.SerializerMethodField()

    client_id = serializers.PrimaryKeyRelatedField(
        queryset=core_models.Client.objects.all(),
        source='client',
        write_only=True
    )

    storage_id = serializers.PrimaryKeyRelatedField(
        queryset=storage_models.Storage.objects.all(),
        source='storage',
        write_only=True,
        required=False
    )

    class Meta:
        model = order_models.Entry
        fields = "__all__"
        read_only_fields = ['code']

    def get_bins_count(self, obj):
        return obj.bins.count()


class ExitProductGroupSerializer(serializers.ModelSerializer):
    product_name = serializers.SerializerMethodField()
    product_uom = serializers.SerializerMethodField()
    product_sku = serializers.SerializerMethodField()
    bin_details = serializers.SerializerMethodField()

    class Meta:
        model = order_models.ExitProductGroup
        fields = ['id', 'exit', 'product', 'product_name', 'product_sku',
                  'product_uom', 'quantity', 'bin_or_batch', 'bin_details']
        read_only_fields = ['bin_details']

    def get_product_name(self, obj):
        return obj.product.name if obj.product else None

    def get_product_sku(self, obj):
        return obj.product.client_sku if obj.product else None

    def get_product_uom(self, obj):
        return obj.product.uom if obj.product else None

    def get_bin_details(self, obj):
        return [{
            'bin_id': ep.bin_product.bin.id,
            'bin_code': ep.bin_product.bin.code,
            'quantity': ep.quantity,
            'is_picking': ep.is_picking
        } for ep in obj.exit_products.all()]


class ExitProductGroupWithDetailsSerializer(serializers.ModelSerializer):
    product_name = serializers.SerializerMethodField()
    product_sku = serializers.SerializerMethodField()
    product_uom = serializers.SerializerMethodField()
    exit_products = serializers.SerializerMethodField()

    class Meta:
        model = order_models.ExitProductGroup
        fields = ['id', 'product', 'product_name', 'product_sku',
                  'product_uom', 'quantity', 'bin_or_batch', 'exit_products']

    def get_product_name(self, obj):
        return obj.product.name if obj.product else None

    def get_product_sku(self, obj):
        return obj.product.client_sku if obj.product else None

    def get_product_uom(self, obj):
        return obj.product.uom if obj.product else None

    def get_exit_products(self, obj):
        from apps.inventory.models import BinProduct
        from apps.core.choices import BinStatus
        from django.db.models import Sum

        products = obj.exit_products.all()
        result = []

        for ep in products:
            product = ep.bin_product.entry_product.product
            client = obj.exit.client
            has_conflict = ep.bin_product.quantity < ep.quantity

            # Calcular cantidades reservadas en órdenes pendientes (solo no procesados)
            reserved_quantity = order_models.ExitProduct.objects.filter(
                exit_product_group__exit__client=client,
                exit_product_group__exit__status__in=[
                    'pendiente', 'en_progreso'],
                bin_product__entry_product__product=product,
                is_processed=False  # Solo no procesados
            ).aggregate(total=Sum('quantity'))['total'] or 0

            # Calcular cantidad total disponible en inventario
            total_available = BinProduct.objects.filter(
                entry_product__product=product,
                entry_product__entry__client=client,
                bin__status=BinStatus.ALMACENADO,
                bin__is_active=True
            ).aggregate(total=Sum('quantity'))['total'] or 0

            # Si hay conflicto, buscar el bin alternativo que se usará
            alternative_bin_info = None
            if has_conflict and not ep.is_processed:
                original_batch = ep.bin_product.entry_product.batch

                # Excluir bins completos ya en otras salidas
                bins_completos_en_otras = order_models.ExitProduct.objects.filter(
                    exit_product_group__exit__client=client,
                    exit_product_group__exit__status__in=[
                        'pendiente', 'en_progreso'],
                    bin_product__entry_product__product=product,
                    is_picking=False
                ).exclude(id=ep.id).values_list('bin_product__bin_id', flat=True)

                base_query = BinProduct.objects.filter(
                    entry_product__product=product,
                    entry_product__entry__client=client,
                    bin__status=BinStatus.ALMACENADO,
                    bin__is_active=True,
                    quantity__gte=ep.quantity
                ).exclude(bin_id__in=bins_completos_en_otras)

                # Buscar primero con mismo lote
                alternative = None
                if original_batch:
                    alternative = base_query.filter(
                        entry_product__batch=original_batch
                    ).order_by('entry_product__expiration_date').first()

                if not alternative:
                    alternative = base_query.order_by(
                        'entry_product__expiration_date').first()

                if alternative:
                    alternative_bin_info = {
                        'bin_code': alternative.bin.code,
                        'bin_id': alternative.bin.id,
                        'position': alternative.bin.position.code if alternative.bin.position else None,
                        'quantity': alternative.quantity,
                        'batch': alternative.entry_product.batch,
                        'same_batch': alternative.entry_product.batch == original_batch if original_batch else False
                    }

            result.append({
                'id': ep.id,
                'bin_code': ep.bin_product.bin.code,
                'bin_id': ep.bin_product.bin.id,
                'status': ep.bin_product.bin.get_status_display(),
                'position': ep.bin_product.bin.position.code if ep.bin_product.bin.position else None,
                'batch': ep.bin_product.batch,
                'expiration_date': ep.bin_product.expiration_date,
                'quantity': ep.quantity,
                'available_quantity': ep.bin_product.quantity,
                'has_stock_conflict': has_conflict,
                # Cantidad reservada en órdenes pendientes
                'reserved_in_pending_orders': reserved_quantity,
                'total_inventory_available': total_available,  # Total disponible en inventario
                # Info del bin alternativo si hay conflicto
                'alternative_bin': alternative_bin_info,
                'client_name': client.name if client else None,
                'product_name': ep.bin_product.entry_product.product.name,
                'product_sku': ep.bin_product.entry_product.product.client_sku,
                'product_uom': ep.bin_product.entry_product.product.uom,
                'is_picking': ep.is_picking,
                'is_processed': ep.is_processed,
                'was_qr_validated': ep.was_qr_validated,
            })

        return result


class ExitProductSerializer(serializers.ModelSerializer):
    bin_product_details = serializers.SerializerMethodField()

    class Meta:
        model = order_models.ExitProduct
        fields = ['id', 'exit_product_group', 'bin_product',
                  'bin_product_details', 'quantity', 'is_processed', 'was_qr_validated', 'is_picking']

    def get_bin_product_details(self, obj):
        if not obj.bin_product:
            return None

        return {
            'id': obj.bin_product.id,
            'bin_code': obj.bin_product.bin.code,
            'product_name': obj.bin_product.entry_product.product.name,
            'uom': obj.bin_product.entry_product.product.uom
        }


class ExitProductSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = order_models.ExitProduct
        fields = ['id', 'quantity', 'is_processed',
                  'was_qr_validated', 'is_picking']

    def to_representation(self, instance):
        from apps.inventory.api.serializers import BinProductSimpleSerializer
        representation = super().to_representation(instance)
        representation['bin_product'] = BinProductSimpleSerializer(
            instance.bin_product).data
        return representation


class ExitDetailSerializer(serializers.ModelSerializer):
    exit_id = serializers.PrimaryKeyRelatedField(
        queryset=order_models.Exit.objects.all(),
        source='exit',
        write_only=True
    )

    class Meta:
        model = order_models.ExitDetail
        exclude = ('created_by', 'updated_by')
        extra_kwargs = {'exit': {'read_only': True}}


class ExitDetailSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = order_models.ExitDetail
        fields = ['id', 'temperature', 'picking_pallets',
                  'description', 'image', 'pdf_document']


class ExitSerializer(serializers.ModelSerializer):
    client = ClientSimpleSerializer(read_only=True)
    branch = BranchSimpleSerializer(read_only=True)
    exit_product_groups = ExitProductGroupWithDetailsSerializer(
        many=True, read_only=True)
    exit_detail = ExitDetailSimpleSerializer(read_only=True)
    updated_by_name = serializers.SerializerMethodField(read_only=True)

    client_id = serializers.PrimaryKeyRelatedField(
        queryset=core_models.Client.objects.all(),
        source='client',
        write_only=True
    )

    branch_id = serializers.PrimaryKeyRelatedField(
        queryset=core_models.Branch.objects.all(),
        source='branch',
        write_only=True,
        required=False
    )

    exit_date = serializers.DateField(
        required=False,
        allow_null=True,
        error_messages={
            'invalid': 'Formato de fecha inválido. Use YYYY-MM-DD.'
        }
    )

    class Meta:
        model = order_models.Exit
        exclude = ('created_by', 'updated_by')
        read_only_fields = ['code']

    def validate(self, attrs):
        # La fecha de salida es obligatoria al crear
        if self.instance is None:  # Es una creación
            if not attrs.get('exit_date'):
                raise serializers.ValidationError({
                    'exit_date': 'La fecha de salida es obligatoria.'
                })
        else:  # Es una actualización
            # Si se está intentando poner la fecha en null, validar
            if 'exit_date' in attrs and attrs['exit_date'] is None:
                raise serializers.ValidationError({
                    'exit_date': 'La fecha de salida es obligatoria.'
                })
        return attrs

    def get_updated_by_name(self, obj):
        if obj.updated_by:
            return obj.updated_by.get_full_name() or obj.updated_by.username
        return None


class UserSimpleSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source='get_full_name', read_only=True)

    class Meta:
        model = core_models.CustomUser
        fields = ['id', 'username', 'full_name']


class ExitMovementSerializer(serializers.ModelSerializer):
    """Serializer para el historial de movimientos de órdenes de salida."""
    created_by = UserSimpleSerializer(read_only=True)

    movement_type_display = serializers.CharField(
        source='get_movement_type_display',
        read_only=True
    )

    old_status_display = serializers.CharField(
        source='get_old_status_display',
        read_only=True
    )

    new_status_display = serializers.CharField(
        source='get_new_status_display',
        read_only=True
    )

    # Información del bin
    bin_position = serializers.SerializerMethodField()

    # Información del cliente de la salida
    client_name = serializers.SerializerMethodField()

    # Código de la salida
    exit_code = serializers.CharField(source='exit.code', read_only=True)

    def get_bin_position(self, obj):
        if obj.bin and obj.bin.position:
            return obj.bin.position.code
        return None

    def get_client_name(self, obj):
        if obj.exit and obj.exit.client:
            return obj.exit.client.name
        return None

    class Meta:
        model = order_models.ExitMovement
        fields = [
            'id',
            'movement_type',
            'movement_type_display',
            'old_status',
            'old_status_display',
            'new_status',
            'new_status_display',
            'product_sku',
            'product_name',
            'bin_code',
            'bin_position',
            'old_bin_code',
            'new_bin_code',
            'quantity',
            'old_quantity',
            'new_quantity',
            'is_picking',
            'notes',
            'metadata',
            'exit_code',
            'client_name',
            'created_at',
            'created_by',
        ]
