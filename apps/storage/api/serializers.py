from django.db import transaction
from rest_framework import serializers
from apps.storage import models as storage_models
from apps.inventory import models as inventory_models


class TokenField(serializers.CharField):
    """Acepta el número que todavía manda el formulario de posiciones."""

    def to_internal_value(self, data):
        if isinstance(data, bool):
            self.fail("invalid")
        if isinstance(data, int):
            data = str(data)
        elif isinstance(data, float) and data.is_integer():
            data = str(int(data))
        return super().to_internal_value(data)


class StorageSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = storage_models.Storage
        fields = ['id', 'name']


class CavaSimpleSerializer(serializers.ModelSerializer):
    storage = StorageSimpleSerializer(read_only=True)

    class Meta:
        model = storage_models.Cava
        fields = ['id', 'name', 'code', 'condition', 'storage', 'current_temp']


class StorageSerializer(serializers.ModelSerializer):
    class Meta:
        model = storage_models.Storage
        exclude = ("created_by", "updated_by")
        read_only_fields = ("workspace", "created_at", "updated_at")


class CavaGenerationJobSerializer(serializers.ModelSerializer):
    detail = serializers.SerializerMethodField()

    class Meta:
        model = storage_models.CavaGenerationJob
        fields = (
            "id",
            "status",
            "detail",
            "expected_count",
            "created_count",
            "skipped_count",
            "created_at",
        )

    def get_detail(self, obj):
        if obj.status in (
            storage_models.CavaGenerationJob.Status.PENDING,
            storage_models.CavaGenerationJob.Status.RUNNING,
        ):
            return "En proceso. Puedes continuar mientras se crean."
        if obj.status == storage_models.CavaGenerationJob.Status.SUCCEEDED:
            return "Posiciones creadas."
        return "No se pudieron crear las posiciones."


class CavaBlockSerializer(serializers.ModelSerializer):
    class Meta:
        model = storage_models.CavaBlock
        fields = (
            "id",
            "sort_order",
            "kind",
            "code",
            "rack_count",
            "levels",
            "depth",
            "orientation",
            "label_side",
            "aisle_code",
        )
        read_only_fields = ("id", "sort_order")

    def validate(self, attrs):
        kind = attrs.get("kind")
        if kind != storage_models.CavaBlock.Kind.RACK:
            return attrs
        levels = attrs.get("levels") or 0
        depth = attrs.get("depth") or 0
        code = str(attrs.get("code") or "").strip()
        count = attrs.get("rack_count") or 1
        if levels < 1 or depth < 1:
            raise serializers.ValidationError("Indique niveles y profundidad del rack.")
        if not code:
            raise serializers.ValidationError("Indique el código del rack.")
        if count > 1 and not code.isdigit():
            raise serializers.ValidationError("Para varios racks el código inicial debe ser un número.")
        return attrs


class PositionNomenclatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = storage_models.PositionNomenclature
        fields = (
            "separator",
            "cava_prefix",
            "aisle_prefix",
            "rack_prefix",
            "level_prefix",
        )


class CavaSerializer(serializers.ModelSerializer):
    storage = StorageSimpleSerializer(read_only=True)
    storage_id = serializers.PrimaryKeyRelatedField(
        source="storage",
        queryset=storage_models.Storage.objects.all(),
        write_only=True,
    )
    blocks = CavaBlockSerializer(many=True, required=False)
    slot_count = serializers.SerializerMethodField()

    class Meta:
        model = storage_models.Cava
        exclude = ("created_by", "updated_by")

    def get_slot_count(self, obj):
        count = getattr(obj, "position_count", None)
        if count is not None:
            return count
        return obj.positions.count()

    def create(self, validated_data):
        blocks = validated_data.pop("blocks", None)
        cava = super().create(validated_data)
        self._replace_blocks(cava, blocks)
        return cava

    def update(self, instance, validated_data):
        blocks = validated_data.pop("blocks", None)
        cava = super().update(instance, validated_data)
        self._replace_blocks(cava, blocks)
        return cava

    def _replace_blocks(self, cava, blocks):
        if blocks is None:
            return
        with transaction.atomic():
            cava.blocks.all().delete()
            storage_models.CavaBlock.objects.bulk_create(
            [
                storage_models.CavaBlock(
                    cava=cava,
                    sort_order=index,
                    kind=block["kind"],
                    code=str(block.get("code") or "").strip(),
                    rack_count=block.get("rack_count") or 1,
                    levels=block.get("levels"),
                    depth=block.get("depth"),
                    orientation=block.get("orientation") or storage_models.CavaBlock.Orientation.HORIZONTAL,
                    label_side=block.get("label_side") or storage_models.CavaBlock.LabelSide.BELOW,
                    aisle_code=str(block.get("aisle_code") or "").strip(),
                )
                for index, block in enumerate(blocks)
            ]
        )


class PositionSerializer(serializers.ModelSerializer):
    cava = CavaSimpleSerializer(read_only=True)
    cava_id = serializers.PrimaryKeyRelatedField(
        source='cava', queryset=storage_models.Cava.objects.all(), write_only=True
    )
    hall = TokenField(max_length=32, allow_blank=True, required=False)
    rack = TokenField(max_length=32)
    place = TokenField(max_length=32)

    def validate_rack(self, value):
        return str(value).strip()

    def validate_hall(self, value):
        return str(value or "").strip()

    def validate_place(self, value):
        return str(value).strip()

    class BinMiniSerializer(serializers.ModelSerializer):
        client = serializers.SerializerMethodField()
        entry_code = serializers.SerializerMethodField()

        class Meta:
            model = inventory_models.Bin
            fields = ['id', 'code', 'status',
                      'dispatched_date', 'client', 'entry_code']

        def get_client(self, obj):
            if obj.client:
                return {'id': obj.client_id, 'name': obj.client.name}
            return None

        def get_entry_code(self, obj):
            return obj.entry.code if obj.entry else None

    bins = BinMiniSerializer(many=True, read_only=True)

    class Meta:
        model = storage_models.Position
        exclude = ('created_by', 'updated_by')

    def _sync_conduit_is_push(self, position, is_push: bool):
        from django.core.exceptions import ValidationError as DjangoValidationError

        from apps.storage.utils.conduits import set_conduit_is_push

        try:
            set_conduit_is_push(position, is_push)
        except DjangoValidationError as exc:
            msg = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            raise serializers.ValidationError({"is_push": msg}) from exc

    def create(self, validated_data):
        # No marcar push en la fila hasta validar profundidad del conducto.
        is_push = validated_data.pop("is_push", False)
        validated_data["is_push"] = False
        position = super().create(validated_data)
        if is_push:
            self._sync_conduit_is_push(position, True)
            position.is_push = True
        return position

    def update(self, instance, validated_data):
        sync_push = "is_push" in validated_data
        is_push = validated_data.pop("is_push") if sync_push else None
        position = super().update(instance, validated_data)
        if sync_push:
            self._sync_conduit_is_push(position, is_push)
            position.is_push = is_push
        return position
