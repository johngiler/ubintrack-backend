from rest_framework import serializers
from django.db.models import Sum

from apps.inventory import models as inventory_models
from apps.core import models as core_models
from apps.order import models as order_models
from apps.storage import models as storage_models
from apps.core.api import serializers as core_serializers
from apps.core import choices


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = inventory_models.Category
        fields = ('id', 'name', 'category_code')


class ProductSerializer(serializers.ModelSerializer):
    code = serializers.CharField(read_only=True)
    category = CategorySerializer(read_only=True)
    client = core_serializers.ClientSerializer(read_only=True)
    inventory_total = serializers.SerializerMethodField()
    inventory_bins = serializers.SerializerMethodField()

    client_id = serializers.PrimaryKeyRelatedField(
        source='client',
        queryset=core_models.Client.objects.all(),
        write_only=True
    )

    category_id = serializers.PrimaryKeyRelatedField(
        source='category',
        queryset=inventory_models.Category.objects.all(),
        write_only=True,
        required=False,
        allow_null=True
    )

    class Meta:
        model = inventory_models.Product
        exclude = ('created_by', 'updated_by',)

    def get_inventory_total(self, obj):
        """Retorna el total de unidades en bins almacenados"""
        total = inventory_models.BinProduct.objects.filter(
            entry_product__product=obj,
            bin__status='almacenado'
        ).aggregate(total=Sum('quantity'))['total']
        return total or 0

    def get_inventory_bins(self, obj):
        """Retorna la lista de bins almacenados con sus cantidades y reservas"""
        from apps.order.models import ExitProduct
        from apps.core.choices import OrderStatus

        bins = inventory_models.BinProduct.objects.filter(
            entry_product__product=obj,
            bin__status='almacenado'
        ).select_related('bin', 'bin__position').order_by('-bin__created_at')

        result = []
        for bep in bins:
            # Obtener reservas para este BinProduct
            reservations = ExitProduct.objects.filter(
                bin_product=bep,
                is_processed=False,
                exit_product_group__exit__status__in=[
                    OrderStatus.PENDIENTE, OrderStatus.EN_PROGRESO
                ]
            ).select_related('exit_product_group__exit').values(
                'exit_product_group__exit__id',
                'exit_product_group__exit__code'
            ).annotate(
                total_quantity=Sum('quantity')
            ).order_by('exit_product_group__exit__code')

            reservations_list = [
                {
                    'exit_id': r['exit_product_group__exit__id'],
                    'exit_code': r['exit_product_group__exit__code'],
                    'quantity': r['total_quantity']
                }
                for r in reservations
            ]

            total_reserved = sum(r['quantity'] for r in reservations_list)

            result.append({
                'id': bep.bin.id,
                'bep_id': bep.id,
                'code': bep.bin.code,
                'quantity': bep.quantity,
                'reserved': total_reserved,
                'available': max(0, bep.quantity - total_reserved),
                'position': bep.bin.position.code if bep.bin.position else None,
                'batch': bep.batch,
                'expiration_date': bep.expiration_date.strftime('%d/%m/%Y') if bep.expiration_date else None,
                'reservations': reservations_list
            })

        return result


class ProductSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = inventory_models.Product
        fields = ['id', 'name', 'uom', 'client_sku']


class EntryProductSimpleSerializer(serializers.ModelSerializer):
    product = ProductSimpleSerializer(read_only=True)
    is_completed = serializers.BooleanField(read_only=True)
    in_inventory = serializers.IntegerField(read_only=True)

    class Meta:
        model = order_models.EntryProduct
        fields = [
            'id',
            'production_date',
            'expiration_date',
            'batch',
            'product',
            'quantity',
            'is_completed',
            'in_inventory',
        ]


class BinProductSerializer(serializers.ModelSerializer):
    bin_id = serializers.PrimaryKeyRelatedField(
        queryset=inventory_models.Bin.objects.all(),
        source='bin',
        write_only=True
    )
    entry_product_id = serializers.PrimaryKeyRelatedField(
        queryset=order_models.EntryProduct.objects.all(),
        source='entry_product',
        write_only=True
    )
    entry_product = EntryProductSimpleSerializer(read_only=True)

    class Meta:
        model = inventory_models.BinProduct
        fields = ['id', 'bin_id', 'entry_product_id', 'quantity',
                  'entry_product', 'batch', 'expiration_date']

    def validate(self, data):
        # Validar que el entry_product pertenece a la misma entrada que el bin
        if data['bin'].entry != data['entry_product'].entry:
            raise serializers.ValidationError(
                "El entry_product debe pertenecer a la misma entrada que el bin"
            )

        # Validar que la cantidad sea mayor que cero
        quantity = data.get('quantity', 0)
        if quantity < 0:
            raise serializers.ValidationError({
                'quantity': "La cantidad debe ser mayor o igual a cero."
            })

        # Calcular cantidad total en bins para validar
        entry_product = data['entry_product']

        # Calcular la cantidad total inventariada para este producto en todos los bins
        total_in_bins = 0
        if self.instance:  # Si es una actualización
            # Excluir la cantidad actual para no contarla dos veces
            total_in_bins = inventory_models.BinProduct.objects.filter(
                entry_product=entry_product,
                is_active=True
            ).exclude(pk=self.instance.pk).aggregate(total=Sum('quantity'))['total'] or 0
        else:  # Si es nuevo
            total_in_bins = inventory_models.BinProduct.objects.filter(
                entry_product=entry_product,
                is_active=True
            ).aggregate(total=Sum('quantity'))['total'] or 0

        # Calcular la cantidad total que se almacenará después de guardar
        total_in_bins_after_save = total_in_bins + quantity

        # Obtener la cantidad ya despachada (para validar contra el total almacenado)
        from apps.order.models import ExitProduct
        total_despachado = ExitProduct.objects.filter(
            bin_product__entry_product=entry_product,
            is_processed=True
        ).aggregate(total=Sum('quantity'))['total'] or 0

        # La cantidad total almacenada será: cantidad en bins + cantidad despachada
        total_almacenado_after_save = total_in_bins_after_save + total_despachado

        # Verificar que la cantidad total almacenada no exceda la cantidad en la entrada
        if total_almacenado_after_save > entry_product.real_quantity:
            entry = entry_product.entry
            raise serializers.ValidationError({
                'quantity': (
                    f"La cantidad total almacenada ({total_almacenado_after_save}) excede la cantidad "
                    f"disponible ({entry_product.real_quantity}) en el producto de entrada. "
                    f"(En bins: {total_in_bins_after_save}, Despachado: {total_despachado})"
                ),
                'entry_id': entry.id,
                'entry_code': entry.code,
                'inconsistency_error': True
            })

        return data

class BinProductSimpleSerializer(serializers.ModelSerializer):
    entry_product = EntryProductSimpleSerializer(read_only=True)
    reservations = serializers.SerializerMethodField()

    class Meta:
        model = inventory_models.BinProduct
        fields = ['id', 'entry_product',
                  'quantity', 'batch', 'expiration_date', 'reservations']

    def get_reservations(self, obj):
        """Obtiene las reservas (salidas con bins no procesados) para este BinProduct"""
        from apps.order.models import ExitProduct
        from apps.core.choices import OrderStatus

        # Obtener ExitProducts no procesados relacionados a este BinProduct
        exit_products = ExitProduct.objects.filter(
            bin_product=obj,
            exit_product_group__exit__status__in=[
                OrderStatus.PENDIENTE, OrderStatus.EN_PROGRESO],
            is_processed=False
        ).select_related('exit_product_group__exit').values(
            'exit_product_group__exit__id',
            'exit_product_group__exit__code',
            'quantity'
        )

        # Agrupar por salida
        reservations_by_exit = {}
        for ep in exit_products:
            exit_id = ep['exit_product_group__exit__id']
            exit_code = ep['exit_product_group__exit__code']
            quantity = ep['quantity']

            if exit_id not in reservations_by_exit:
                reservations_by_exit[exit_id] = {
                    'exit_id': exit_id,
                    'exit_code': exit_code,
                    'total_quantity': 0
                }
            reservations_by_exit[exit_id]['total_quantity'] += quantity

        # Convertir a lista
        return list(reservations_by_exit.values())


class ClientSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = core_models.Client
        fields = ['id', 'name']


class EntrySimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = order_models.Entry
        fields = ['id', 'code']


class PositionSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = storage_models.Position
        fields = ['id', 'code']


class BinSerializer(serializers.ModelSerializer):
    client = ClientSimpleSerializer(read_only=True)
    entry = EntrySimpleSerializer(read_only=True)
    bin_products = BinProductSimpleSerializer(
        many=True, read_only=True)
    position = PositionSimpleSerializer(read_only=True)
    related_exits = serializers.SerializerMethodField()
    tag_image = serializers.SerializerMethodField()

    client_id = serializers.PrimaryKeyRelatedField(
        queryset=core_models.Client.objects.all(),
        source='client',
        write_only=True
    )

    entry_id = serializers.PrimaryKeyRelatedField(
        queryset=order_models.Entry.objects.all(),
        source='entry',
        write_only=True
    )

    position_id = serializers.PrimaryKeyRelatedField(
        queryset=storage_models.Position.objects.all(),
        source='position',
        write_only=True,
        required=False,
        allow_null=True
    )

    class Meta:
        model = inventory_models.Bin
        exclude = ('created_by', 'updated_by',)
        read_only_fields = ('workspace', 'code', 'created_at', 'updated_at')

    def get_tag_image(self, obj):
        from apps.common.utils.media_layout import media_url_with_bust

        return media_url_with_bust(
            obj.tag_image,
            request=self.context.get("request"),
            version=getattr(obj, "updated_at", None),
        )

    def get_related_exits(self, obj):
        """Obtiene las órdenes de salida relacionadas a este bin"""
        from apps.order.models import ExitProduct
        exit_products = ExitProduct.objects.filter(
            bin_product__bin=obj
        ).select_related('exit_product_group__exit').distinct()

        exits = []
        seen_exit_ids = set()
        for ep in exit_products:
            exit_obj = ep.exit_product_group.exit
            if exit_obj.id not in seen_exit_ids:
                seen_exit_ids.add(exit_obj.id)
                exits.append({
                    'exit_id': exit_obj.id,
                    'exit_code': exit_obj.code,
                    'exit_status': exit_obj.status,
                })
        return exits

    def validate_position_id(self, position):
        if self.instance:
            current_position = self.instance.position
            if position and position.status != choices.PositionStatus.LIBRE and (
                not current_position or position.code != current_position.code
            ):
                raise serializers.ValidationError(
                    f"La posición {position.code} no está libre. Estado actual: {position.status}"
                )
        elif position and position.status != choices.PositionStatus.LIBRE:
            raise serializers.ValidationError(
                f"La posición {position.code} no está libre. Estado actual: {position.status}"
            )
        return position

    def validate(self, attrs):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from apps.storage.utils.push_validation import validate_push_assignment

        attrs = super().validate(attrs)
        # Solo validar push si la posición cambia (no en cada save de LPN/obs/etc.).
        if "position" in attrs and attrs["position"] is not None and self.instance:
            current = self.instance.position
            if current is None or current.pk != attrs["position"].pk:
                try:
                    validate_push_assignment(
                        attrs["position"],
                        self.instance,
                        exclude_bin_id=self.instance.pk,
                    )
                except DjangoValidationError as exc:
                    raise serializers.ValidationError(
                        {
                            "position_id": exc.messages
                            if hasattr(exc, "messages")
                            else [str(exc)]
                        }
                    )
        return attrs

    def create(self, validated_data):
        from apps.storage.models import Position
        from django.utils import timezone
        from django.core.exceptions import ValidationError as DjangoValidationError
        from apps.storage.utils.push_validation import validate_push_assignment

        position = validated_data.get('position')
        bin_entry = super().create(validated_data)

        if position:
            try:
                validate_push_assignment(position, bin_entry, exclude_bin_id=bin_entry.pk)
            except DjangoValidationError as exc:
                bin_entry.delete()
                raise serializers.ValidationError(
                    {'position_id': exc.messages if hasattr(exc, 'messages') else [str(exc)]}
                )
            Position.objects.filter(pk=position.pk).update(
                status=choices.PositionStatus.ASIGNADO,
                updated_at=timezone.now()
            )

        return bin_entry

    def update(self, instance, validated_data):
        from apps.storage.models import Position
        from django.utils import timezone
        from django.core.exceptions import ValidationError as DjangoValidationError
        from apps.storage.utils.push_validation import validate_push_assignment

        # Si se está cambiando la posición
        old_position = instance.position
        new_position = validated_data.get('position', old_position)

        # Obtener el estado anterior y el nuevo (si se está actualizando)
        old_status = instance.status
        new_status = validated_data.get('status', old_status)

        if (
            "position" in validated_data
            and new_position is not None
            and (old_position is None or old_position.pk != new_position.pk)
        ):
            try:
                validate_push_assignment(
                    new_position, instance, exclude_bin_id=instance.pk
                )
            except DjangoValidationError as exc:
                raise serializers.ValidationError(
                    {
                        "position_id": exc.messages
                        if hasattr(exc, "messages")
                        else [str(exc)]
                    }
                )

        # Actualizar el bin entry
        bin = super().update(instance, validated_data)

        # Si la posición cambió
        if old_position != new_position:
            # Liberar la posición anterior si existía
            if old_position:
                Position.objects.filter(pk=old_position.pk).update(
                    status=choices.PositionStatus.LIBRE,
                    updated_at=timezone.now()
                )

            # Asignar la nueva posición si existe
            if new_position:
                # Si el bin está en estado ALMACENADO, la posición debe estar OCUPADA
                if bin.status == choices.BinStatus.ALMACENADO:
                    new_status_pos = choices.PositionStatus.OCUPADO
                else:
                    new_status_pos = choices.PositionStatus.ASIGNADO
                Position.objects.filter(pk=new_position.pk).update(
                    status=new_status_pos,
                    updated_at=timezone.now()
                )

        # Si solo cambió el estado pero no la posición, y hay una posición asignada
        elif old_status != new_status and bin.position:
            # Si el nuevo estado es ALMACENADO, actualizar la posición a OCUPADO
            if new_status == choices.BinStatus.ALMACENADO:
                Position.objects.filter(pk=bin.position.pk).update(
                    status=choices.PositionStatus.OCUPADO,
                    updated_at=timezone.now()
                )
            # Si el nuevo estado es EN_DESPACHO o DESPACHADO, liberar la posición
            elif new_status in [choices.BinStatus.EN_DESPACHO, choices.BinStatus.DESPACHADO]:
                Position.objects.filter(pk=bin.position.pk).update(
                    status=choices.PositionStatus.LIBRE,
                    updated_at=timezone.now()
                )
            # Si era ALMACENADO pero ahora es EN_RECIBO, cambiar a ASIGNADO
            elif old_status == choices.BinStatus.ALMACENADO and new_status == choices.BinStatus.EN_RECIBO:
                Position.objects.filter(pk=bin.position.pk).update(
                    status=choices.PositionStatus.ASIGNADO,
                    updated_at=timezone.now()
                )

        return bin


class BinSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = inventory_models.Bin
        fields = ['id', 'code']


class ExitProductSimpleSerializer(serializers.ModelSerializer):
    bin_product = BinProductSimpleSerializer(read_only=True)

    class Meta:
        model = order_models.ExitProduct
        fields = ['id', 'bin_product', 'quantity', 'is_picking']


class ExitSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = order_models.Exit
        fields = ['id', 'code']


class UserSimpleSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = core_models.CustomUser
        fields = ['id', 'username', 'full_name']

    def get_full_name(self, obj) -> str:
        from apps.core.utils.display_name import user_display_name

        return user_display_name(obj)


class BinMovementSerializer(serializers.ModelSerializer):
    created_by = UserSimpleSerializer(read_only=True)
    exit = ExitSimpleSerializer(read_only=True)

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

    position_code = serializers.CharField(
        source='position.code', read_only=True
    )

    # Información del bin
    bin_code = serializers.CharField(
        source='bin.code', read_only=True
    )
    bin_status = serializers.CharField(
        source='bin.status', read_only=True
    )

    # Información del producto (desde bin_products)
    product_name = serializers.SerializerMethodField()
    product_sku = serializers.SerializerMethodField()
    product_id = serializers.SerializerMethodField()
    product_uom = serializers.SerializerMethodField()

    # Información de la entrada
    entry_code = serializers.SerializerMethodField()
    entry_id = serializers.SerializerMethodField()

    # Información del cliente
    client_name = serializers.SerializerMethodField()

    def get_client_name(self, obj):
        if obj.bin and obj.bin.client:
            return obj.bin.client.name
        return None

    def get_product_name(self, obj):
        bin_product = obj.bin.bin_products.first()
        if bin_product and bin_product.entry_product and bin_product.entry_product.product:
            return bin_product.entry_product.product.name
        return None

    def get_product_sku(self, obj):
        bin_product = obj.bin.bin_products.first()
        if bin_product and bin_product.entry_product and bin_product.entry_product.product:
            return bin_product.entry_product.product.client_sku
        return None

    def get_product_id(self, obj):
        bin_product = obj.bin.bin_products.first()
        if bin_product and bin_product.entry_product and bin_product.entry_product.product:
            return bin_product.entry_product.product.id
        return None

    def get_product_uom(self, obj):
        bin_product = obj.bin.bin_products.first()
        if bin_product and bin_product.entry_product and bin_product.entry_product.product:
            return bin_product.entry_product.product.uom
        return None

    def get_entry_code(self, obj):
        if obj.bin and obj.bin.entry:
            return obj.bin.entry.code
        return None

    def get_entry_id(self, obj):
        if obj.bin and obj.bin.entry:
            return obj.bin.entry.id
        return None

    class Meta:
        model = inventory_models.BinMovement
        fields = [
            'id',
            'movement_type',
            'movement_type_display',
            'old_status',
            'old_status_display',
            'new_status',
            'new_status_display',
            'exit',
            'exit_code',
            'is_picking',
            'quantity_subtracted',
            'quantity_remaining',
            'initial_quantity',
            'position',
            'position_code',
            'notes',
            'created_at',
            'created_by',
            # Nuevos campos
            'bin_code',
            'bin_status',
            'product_name',
            'product_sku',
            'product_id',
            'product_uom',
            'entry_code',
            'entry_id',
            'client_name',
        ]
        read_only_fields = ['created_at', 'created_by']
