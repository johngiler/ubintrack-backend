from django.db import transaction
from rest_framework import serializers
from apps.storage import models as storage_models
from apps.inventory import models as inventory_models
from apps.storage.utils.aisle_token import unique_aisle_token
from apps.storage.utils.floor_neighbors import floor_is_active, section_neighbors


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
        fields = ["id", "name"]


class CavaSimpleSerializer(serializers.ModelSerializer):
    storage = StorageSimpleSerializer(read_only=True)

    class Meta:
        model = storage_models.Cava
        fields = ["id", "name", "code", "condition", "storage", "current_temp"]


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
            "section_type",
            "name",
            "code",
            "rack_count",
            "levels",
            "depth",
            "overlap",
            "always_visible",
            "stretch_full",
            "orientation",
            "label_side",
            "aisle_code",
        )
        read_only_fields = ("id", "sort_order")

    def validate(self, attrs):
        kind = attrs.get("kind")
        code = str(attrs.get("code") or "").strip()
        name = str(attrs.get("name") or "").strip()
        count = attrs.get("rack_count")
        if count is None:
            count = 1

        if kind == storage_models.CavaBlock.Kind.SECTION:
            section_type = attrs.get("section_type") or storage_models.CavaBlock.SectionType.PASILLO
            attrs["section_type"] = section_type
            if not name and not code:
                raise serializers.ValidationError("Indique el nombre de la sección.")
            return attrs

        if kind == storage_models.CavaBlock.Kind.FLOOR:
            if count < 1:
                raise serializers.ValidationError("Indique la cantidad de posiciones de piso.")
            across = attrs.get("depth")
            if across is None:
                attrs["depth"] = 3
            else:
                across = int(across or 0)
                if across < 1:
                    raise serializers.ValidationError("Indique la dimensión (por grupo).")
                attrs["depth"] = across
            return attrs

        if kind != storage_models.CavaBlock.Kind.RACK:
            return attrs
        levels = attrs.get("levels") or 0
        depth = attrs.get("depth") or 0
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
    active_slot_count = serializers.SerializerMethodField()

    class Meta:
        model = storage_models.Cava
        exclude = ("created_by", "updated_by")

    def get_slot_count(self, obj):
        count = getattr(obj, "position_count", None)
        if count is not None:
            return count
        return obj.positions.count()

    def get_active_slot_count(self, obj):
        count = getattr(obj, "active_position_count", None)
        if count is not None:
            return count
        return obj.positions.filter(is_active=True).count()

    def validate_blocks(self, blocks):
        if blocks is None:
            return blocks
        for index, block in enumerate(blocks):
            if block.get("kind") != storage_models.CavaBlock.Kind.FLOOR:
                continue
            neighbors = section_neighbors(blocks, index)
            if not floor_is_active(neighbors):
                raise serializers.ValidationError(
                    "Las posiciones de piso requieren una sección vecina con nombre."
                )
            count = int(block.get("rack_count") or 0)
            if count < 1:
                raise serializers.ValidationError(
                    "Indique la cantidad de posiciones de piso."
                )
        return blocks

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
            used_tokens: set[str] = set()
            prepared = []
            for index, block in enumerate(blocks):
                kind = block["kind"]
                name = str(block.get("name") or "").strip()
                code = str(block.get("code") or "").strip()
                section_type = str(block.get("section_type") or "").strip()
                rack_count = block.get("rack_count")

                depth = block.get("depth")
                overlap = False
                always_visible = False
                stretch_full = False
                if kind == storage_models.CavaBlock.Kind.SECTION:
                    if not section_type:
                        section_type = storage_models.CavaBlock.SectionType.PASILLO
                    if not name:
                        name = f"Sección {code}" if code else "Sección"
                    code = unique_aisle_token(name, used_tokens)
                    used_tokens.add(code)
                    rack_count = 0
                    if "stretch_full" in block:
                        stretch_full = bool(block.get("stretch_full"))
                    else:
                        stretch_full = section_type == storage_models.CavaBlock.SectionType.PASILLO
                elif kind == storage_models.CavaBlock.Kind.FLOOR:
                    section_type = ""
                    name = ""
                    code = ""
                    rack_count = int(rack_count or 0)
                    depth = int(depth or 3)
                    if depth < 1:
                        depth = 3
                    overlap = bool(block.get("overlap"))
                    always_visible = bool(block.get("always_visible"))
                else:
                    section_type = ""
                    name = ""
                    rack_count = rack_count or 1

                prepared.append(
                    storage_models.CavaBlock(
                        cava=cava,
                        sort_order=index,
                        kind=kind,
                        section_type=section_type,
                        name=name,
                        code=code,
                        rack_count=rack_count,
                        levels=block.get("levels"),
                        depth=depth,
                        overlap=overlap,
                        always_visible=always_visible,
                        stretch_full=stretch_full,
                        orientation=block.get("orientation")
                        or storage_models.CavaBlock.Orientation.HORIZONTAL,
                        label_side=block.get("label_side")
                        or storage_models.CavaBlock.LabelSide.BELOW,
                        aisle_code=str(block.get("aisle_code") or "").strip(),
                    )
                )
            storage_models.CavaBlock.objects.bulk_create(prepared)


class PositionSerializer(serializers.ModelSerializer):
    cava = CavaSimpleSerializer(read_only=True)
    cava_id = serializers.PrimaryKeyRelatedField(
        source="cava", queryset=storage_models.Cava.objects.all(), write_only=True
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
            fields = ["id", "code", "status", "dispatched_date", "client", "entry_code"]

        def get_client(self, obj):
            if obj.client:
                return {"id": obj.client_id, "name": obj.client.name}
            return None

        def get_entry_code(self, obj):
            return obj.entry.code if obj.entry else None

    bins = BinMiniSerializer(many=True, read_only=True)

    class Meta:
        model = storage_models.Position
        exclude = ("created_by", "updated_by")

    def validate(self, attrs):
        from django.core.exceptions import ValidationError as DjangoValidationError

        from apps.storage.utils.position_flags import validate_position_flags

        instance = self.instance
        is_active = attrs.get(
            "is_active", instance.is_active if instance is not None else True
        )
        is_picking = attrs.get(
            "is_picking", instance.is_picking if instance is not None else False
        )
        is_push = attrs.get(
            "is_push", instance.is_push if instance is not None else False
        )

        # Al desactivar: apagar flags (sin exigirlos en el payload).
        if not is_active:
            attrs["is_push"] = False
            attrs["is_picking"] = False
            is_push = False
            is_picking = False

        try:
            validate_position_flags(
                is_active=bool(is_active),
                is_picking=bool(is_picking),
                is_push=bool(is_push),
            )
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict) from exc
            raise serializers.ValidationError(
                "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            ) from exc
        return attrs

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
        if not validated_data.get("is_active", True):
            validated_data["is_picking"] = False
        position = super().create(validated_data)
        if is_push:
            self._sync_conduit_is_push(position, True)
            position.refresh_from_db(fields=["is_push", "is_picking"])
        return position

    def update(self, instance, validated_data):
        sync_push = "is_push" in validated_data
        is_push = validated_data.pop("is_push") if sync_push else None
        # Si se activa picking con push previo: validate ya bloqueó; si se activa push,
        # el sync de conducto apaga picking en el conducto.
        position = super().update(instance, validated_data)
        if sync_push:
            self._sync_conduit_is_push(position, is_push)
            position.refresh_from_db(fields=["is_push", "is_picking"])
        return position
