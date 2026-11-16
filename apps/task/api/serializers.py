from django.db.models import Q
from rest_framework import serializers

from apps.core import models as core_models
from apps.core.choices import TaskType, UserType
from apps.inventory import models as inventory_models
from apps.storage import models as storage_models
from apps.task import models as task_models
from apps.workspaces.utils.tenant import get_workspace_for_request


def _staff_users_for_workspace(workspace):
    qs = core_models.CustomUser.objects.filter(
        ~Q(user_type=UserType.CLIENT),
        is_active=True,
    )
    if workspace is not None:
        qs = qs.filter(workspace=workspace)
    return qs


def _normalize_halls(value):
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise serializers.ValidationError({"halls": "Formato no válido."})
    cleaned = []
    seen = set()
    for item in value:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        cleaned.append(token)
    return cleaned


class UserSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = core_models.CustomUser
        fields = ["id", "username", "email", "full_name"]


class ClientMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = core_models.Client
        fields = ["id", "name"]


class CavaMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = storage_models.Cava
        fields = ["id", "name", "code"]


class ProductMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = inventory_models.Product
        fields = ["id", "name", "client_sku"]


class TaskLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = task_models.TaskLog
        fields = ["id", "task", "message", "created_at", "updated_at", "is_active"]
        read_only_fields = [
            "id",
            "task",
            "message",
            "created_at",
            "updated_at",
            "is_active",
        ]


class TaskLineSerializer(serializers.ModelSerializer):
    position_code = serializers.CharField(source="position.code", read_only=True)
    bin_code = serializers.CharField(source="bin.code", read_only=True, default=None)

    class Meta:
        model = task_models.TaskLine
        fields = [
            "id",
            "task",
            "position",
            "position_code",
            "bin",
            "bin_code",
            "line_status",
            "sort_order",
            "notes",
        ]
        read_only_fields = ["task"]


class TaskListSerializer(serializers.ModelSerializer):
    assigned_to = UserSimpleSerializer(many=True, read_only=True)
    client = ClientMiniSerializer(read_only=True)
    cavas = CavaMiniSerializer(many=True, read_only=True)
    products = ProductMiniSerializer(many=True, read_only=True)
    lines_count = serializers.IntegerField(read_only=True, default=0)
    lines_done = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = task_models.Task
        exclude = ("created_by", "updated_by")
        read_only_fields = ["code", "workspace"]


class TaskSerializer(serializers.ModelSerializer):
    assigned_to = UserSimpleSerializer(many=True, read_only=True)
    assigned_to_ids = serializers.PrimaryKeyRelatedField(
        queryset=core_models.CustomUser.objects.none(),
        many=True,
        write_only=True,
        source="assigned_to",
        required=False,
    )
    client = ClientMiniSerializer(read_only=True)
    client_id = serializers.PrimaryKeyRelatedField(
        queryset=core_models.Client.objects.none(),
        write_only=True,
        source="client",
        required=False,
        allow_null=True,
    )
    cavas = CavaMiniSerializer(many=True, read_only=True)
    cava_ids = serializers.PrimaryKeyRelatedField(
        queryset=storage_models.Cava.objects.none(),
        many=True,
        write_only=True,
        source="cavas",
        required=False,
    )
    products = ProductMiniSerializer(many=True, read_only=True)
    product_ids = serializers.PrimaryKeyRelatedField(
        queryset=inventory_models.Product.objects.none(),
        many=True,
        write_only=True,
        source="products",
        required=False,
    )
    halls = serializers.ListField(
        child=serializers.CharField(allow_blank=False, max_length=64),
        required=False,
    )
    task_logs = TaskLogSerializer(many=True, read_only=True)
    lines_count = serializers.SerializerMethodField()
    lines_done = serializers.SerializerMethodField()

    class Meta:
        model = task_models.Task
        exclude = ("created_by", "updated_by")
        read_only_fields = ["code", "workspace"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        workspace = get_workspace_for_request(request) if request else None
        self.fields["assigned_to_ids"].queryset = _staff_users_for_workspace(workspace)
        if workspace is not None:
            self.fields["client_id"].queryset = core_models.Client.objects.filter(
                workspace=workspace
            )
            self.fields["cava_ids"].queryset = storage_models.Cava.objects.filter(
                storage__workspace=workspace
            )
            self.fields["product_ids"].queryset = inventory_models.Product.objects.filter(
                client__workspace=workspace
            )

    def get_lines_count(self, obj):
        annotated = getattr(obj, "lines_count", None)
        if annotated is not None:
            return annotated
        return obj.lines.count()

    def get_lines_done(self, obj):
        annotated = getattr(obj, "lines_done", None)
        if annotated is not None:
            return annotated
        from apps.core.choices import TaskLineStatus

        return obj.lines.exclude(line_status=TaskLineStatus.PENDING).count()

    def validate_halls(self, value):
        return _normalize_halls(value)

    def validate(self, attrs):
        workspace = self.instance.workspace if self.instance else attrs.get("workspace")
        if workspace is None:
            request = self.context.get("request")
            workspace = get_workspace_for_request(request) if request else None
        if workspace is None:
            return attrs

        client = attrs.get("client", getattr(self.instance, "client", None))
        if client and client.workspace_id != workspace.id:
            raise serializers.ValidationError({"client_id": "Cliente no válido."})

        cavas = attrs.get("cavas")
        if cavas is not None:
            for cava in cavas:
                if cava.storage.workspace_id != workspace.id:
                    raise serializers.ValidationError({"cava_ids": "Cámara no válida."})

        products = attrs.get("products")
        if products is not None:
            for product in products:
                if product.client.workspace_id != workspace.id:
                    raise serializers.ValidationError(
                        {"product_ids": "Producto no válido."}
                    )
                if client and product.client_id != client.id:
                    raise serializers.ValidationError(
                        {"product_ids": "Producto no válido."}
                    )

        assigned = attrs.get("assigned_to")
        if assigned is not None:
            for user in assigned:
                if user.workspace_id != workspace.id:
                    raise serializers.ValidationError(
                        {"assigned_to_ids": "Asignación no válida."}
                    )

        task_type = attrs.get(
            "task_type", getattr(self.instance, "task_type", None)
        )
        if self.instance is None and task_type == TaskType.INVENTARIO_GENERAL:
            attrs.setdefault("require_approval", True)
            attrs.setdefault("include_empty_positions", True)

        if task_type == TaskType.INVENTARIO_INICIAL and "products" in attrs:
            attrs["products"] = []

        return attrs
