from rest_framework import serializers
from apps.storage import models as storage_models
from apps.inventory import models as inventory_models


class StorageSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = storage_models.Storage
        fields = ['id', 'name']


class CavaSimpleSerializer(serializers.ModelSerializer):
    storage = StorageSimpleSerializer(read_only=True)

    class Meta:
        model = storage_models.Cava
        fields = ['id', 'name', 'condition', 'storage', 'current_temp']


class StorageSerializer(serializers.ModelSerializer):
    class Meta:
        model = storage_models.Storage
        exclude = ("created_by", "updated_by")
        read_only_fields = ("workspace", "created_at", "updated_at")


class CavaSerializer(serializers.ModelSerializer):
    storage = StorageSimpleSerializer(read_only=True)
    storage_id = serializers.PrimaryKeyRelatedField(
        source="storage",
        queryset=storage_models.Storage.objects.all(),
        write_only=True,
    )

    class Meta:
        model = storage_models.Cava
        exclude = ("created_by", "updated_by")


class PositionSerializer(serializers.ModelSerializer):
    cava = CavaSimpleSerializer(read_only=True)
    cava_id = serializers.PrimaryKeyRelatedField(
        source='cava', queryset=storage_models.Cava.objects.all(), write_only=True
    )
    hall = serializers.CharField(max_length=1)
    rack = serializers.IntegerField()
    place = serializers.CharField(max_length=1)

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
