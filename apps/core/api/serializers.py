from rest_framework import serializers
from django.contrib.auth.models import Permission as DjangoPermission

from apps.core import choices
from apps.core import models as core_models
from apps.core.utils.user_roles import get_user_client, is_workspace_administrator
from apps.workspaces.utils.tenant import get_workspace_for_request, user_can_login_frontend


class CustomPermissionSerializer(serializers.ModelSerializer):
    django_permissions = serializers.SerializerMethodField()
    # Campos amigables opcionales desde PermissionMeta
    friendly = serializers.SerializerMethodField()

    class Meta:
        model = core_models.CustomPermission
        exclude = ('created_by', 'updated_by')

    def get_django_permissions(self, obj):
        return [
            {
                'id': perm.id,
                'name': perm.name,
                'codename': perm.codename,
                'content_type': perm.content_type.model,
                'friendly_name': getattr(getattr(perm, 'meta', None), 'friendly_name', None),
                'description': getattr(getattr(perm, 'meta', None), 'description', None),
            } for perm in obj.django_permissions.all()
        ]

    def get_friendly(self, obj):
        return obj.name


class ProfileSerializer(serializers.ModelSerializer):
    permissions = CustomPermissionSerializer(many=True, read_only=True)
    permission_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False
    )
    users_count = serializers.SerializerMethodField()

    class Meta:
        model = core_models.Profile
        exclude = ('created_by', 'updated_by')
        read_only_fields = ('workspace', 'created_at', 'updated_at')

    def validate(self, attrs):
        user_type = attrs.get(
            "user_type",
            getattr(self.instance, "user_type", None),
        )
        valid_types = {
            choices.ProfileUserType.EMPLOYEE,
            choices.ProfileUserType.CLIENT,
        }
        if user_type not in valid_types:
            raise serializers.ValidationError(
                {"user_type": "Seleccione Cliente o Empleado."}
            )
        return attrs

    def get_users_count(self, obj):
        return obj.custom_users.count()

    def create(self, validated_data):
        permission_ids = validated_data.pop('permission_ids', [])
        profile = core_models.Profile.objects.create(**validated_data)
        profile.permissions.set(permission_ids)
        return profile

    def update(self, instance, validated_data):
        permission_ids = validated_data.pop('permission_ids', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if permission_ids is not None:
            instance.permissions.set(permission_ids)

        return instance


class CustomUserSerializer(serializers.ModelSerializer):
    profile = serializers.SerializerMethodField()
    client = serializers.SerializerMethodField()
    profile_id = serializers.IntegerField(source="profile.id", read_only=True)
    client_id = serializers.IntegerField(source="client.id", read_only=True, allow_null=True)
    user_type = serializers.CharField(read_only=True)
    date_joined = serializers.DateTimeField(read_only=True)
    last_login = serializers.DateTimeField(read_only=True)

    class Meta:
        model = core_models.CustomUser
        fields = (
            "id",
            "full_name",
            "email",
            "profile",
            "profile_id",
            "user_type",
            "client",
            "client_id",
            "is_active",
            "is_superuser",
            "date_joined",
            "last_login",
        )

    def get_profile(self, obj):
        if obj.profile:
            return obj.profile.name
        return None

    def get_client(self, obj):
        client = get_user_client(obj)
        if client:
            return {"id": client.id, "name": client.name}
        return None


class CustomUserWriteSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    plain_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    profile_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    client_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = core_models.CustomUser
        fields = (
            "id",
            "full_name",
            "email",
            "user_type",
            "password",
            "plain_password",
            "profile_id",
            "client_id",
            "is_active",
        )
        read_only_fields = ("id",)

    def validate(self, attrs):
        request = self.context.get("request")
        if self.instance and request and self.instance.pk == request.user.pk:
            if (
                "user_type" in attrs
                and attrs["user_type"] != self.instance.user_type
            ):
                raise serializers.ValidationError(
                    {"user_type": "No puede cambiar su propio tipo de usuario."}
                )
            if "is_active" in attrs and not attrs["is_active"]:
                raise serializers.ValidationError(
                    {"is_active": "No puede desactivar su propio usuario."}
                )

        user_type = attrs.get(
            "user_type",
            getattr(self.instance, "user_type", None),
        )
        client_id = attrs.get("client_id", getattr(self.instance, "client_id", None))

        if user_type == choices.UserType.CLIENT and not client_id:
            raise serializers.ValidationError(
                {"client_id": "Requerido para usuarios tipo Cliente."}
            )
        if user_type in (choices.UserType.ADMINISTRATOR, choices.UserType.EMPLOYEE):
            if client_id:
                raise serializers.ValidationError(
                    {"client_id": "No aplica para administrador o empleado."}
                )
        if user_type == choices.UserType.EMPLOYEE:
            profile_id = attrs.get("profile_id")
            if profile_id is None and self.instance is not None:
                profile_id = self.instance.profile_id
            if not profile_id:
                raise serializers.ValidationError(
                    {"profile_id": "Requerido para usuarios tipo Empleado."}
                )
        return attrs

    def _resolve_profile(self, profile_id, workspace):
        if profile_id is None:
            return None
        return core_models.Profile.objects.get(id=profile_id, workspace=workspace)

    def _resolve_client(self, client_id, workspace):
        if client_id is None:
            return None
        return core_models.Client.objects.get(id=client_id, workspace=workspace)

    def _validate_profile_for_user_type(self, user_type, profile):
        if profile is None:
            return
        if user_type == choices.UserType.CLIENT and profile.user_type != choices.ProfileUserType.CLIENT:
            raise serializers.ValidationError(
                {"profile_id": "El perfil debe ser de tipo Cliente."}
            )
        if user_type == choices.UserType.EMPLOYEE and profile.user_type != choices.ProfileUserType.EMPLOYEE:
            raise serializers.ValidationError(
                {"profile_id": "El perfil debe ser de tipo Empleado."}
            )

    def create(self, validated_data):
        request = self.context["request"]
        workspace = get_workspace_for_request(request)
        if workspace is None:
            raise serializers.ValidationError({"workspace": "Owner no resuelto."})

        password = validated_data.pop("password", None) or validated_data.pop("plain_password", None)
        profile_id = validated_data.pop("profile_id", None)
        client_id = validated_data.pop("client_id", None)
        user_type = validated_data["user_type"]
        email = validated_data["email"]

        if not password:
            raise serializers.ValidationError({"password": "Requerida."})

        client = self._resolve_client(client_id, workspace) if client_id else None
        profile = self._resolve_profile(profile_id, workspace) if profile_id else None

        if user_type == choices.UserType.CLIENT and not profile:
            profile = core_models.Profile.objects.filter(
                workspace=workspace,
                name__iexact="Cliente",
                user_type=choices.ProfileUserType.CLIENT,
            ).first()

        self._validate_profile_for_user_type(user_type, profile)

        if user_type == choices.UserType.EMPLOYEE and not profile:
            raise serializers.ValidationError(
                {"profile_id": "Requerido para usuarios tipo Empleado."}
            )

        user = core_models.CustomUser(
            username=email,
            email=email,
            full_name=validated_data.get("full_name"),
            user_type=user_type,
            workspace=client.workspace if client else workspace,
            client=client,
            profile=profile,
            is_active=validated_data.get("is_active", True),
            is_staff=False,
        )
        user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        request = self.context["request"]
        workspace = get_workspace_for_request(request) or instance.workspace
        password = validated_data.pop("password", None) or validated_data.pop("plain_password", None)
        profile_id = validated_data.pop("profile_id", None)
        client_id = validated_data.pop("client_id", None)

        if "email" in validated_data:
            instance.email = validated_data["email"]
            instance.username = validated_data["email"]

        if "full_name" in validated_data:
            instance.full_name = validated_data["full_name"]

        if "user_type" in validated_data:
            instance.user_type = validated_data["user_type"]

        if "is_active" in validated_data:
            instance.is_active = validated_data["is_active"]

        if profile_id is not None:
            instance.profile = (
                self._resolve_profile(profile_id, workspace) if profile_id else None
            )

        user_type = instance.user_type
        if client_id is not None:
            instance.client = (
                self._resolve_client(client_id, workspace) if client_id else None
            )
        elif user_type in (choices.UserType.ADMINISTRATOR, choices.UserType.EMPLOYEE):
            instance.client = None

        if user_type == choices.UserType.CLIENT and instance.client:
            instance.workspace = instance.client.workspace

        self._validate_profile_for_user_type(user_type, instance.profile)

        if user_type == choices.UserType.EMPLOYEE and not instance.profile:
            raise serializers.ValidationError(
                {"profile_id": "Requerido para usuarios tipo Empleado."}
            )

        if password:
            instance.set_password(password)

        instance.save()
        return instance


def build_whoami_payload(user, request):
    ws = get_workspace_for_request(request)
    if ws is None or not user_can_login_frontend(user, ws):
        return None

    client_payload = None
    client = get_user_client(user)
    if client:
        client_payload = {
            "id": client.id,
            "name": client.name,
        }

    perms = []
    if not is_workspace_administrator(user):
        for perm_str in user.get_all_permissions():
            try:
                app_label, codename = perm_str.split(".")
                perm = DjangoPermission.objects.select_related("content_type").get(
                    content_type__app_label=app_label, codename=codename
                )
                perms.append({
                    "key": perm_str,
                    "name": perm.name,
                    "friendly_name": getattr(getattr(perm, "meta", None), "friendly_name", None),
                    "description": getattr(getattr(perm, "meta", None), "description", None),
                })
            except Exception:
                perms.append({"key": perm_str})

    return {
        "id": user.id,
        "email": user.email,
        "full_name": getattr(user, "full_name", None),
        "is_superuser": user.is_superuser,
        "is_workspace_admin": is_workspace_administrator(user),
        "user_type": user.user_type,
        "profile": user.profile.name if user.profile else None,
        "client": client_payload,
        "permissions": perms,
    }


class MeProfileUpdateSerializer(serializers.Serializer):
    full_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    email = serializers.EmailField(required=False)
    password = serializers.CharField(required=False, allow_blank=True, write_only=True)
    password_confirm = serializers.CharField(required=False, allow_blank=True, write_only=True)

    def validate(self, attrs):
        password = attrs.get("password") or ""
        confirm = attrs.get("password_confirm") or ""
        if password or confirm:
            if password != confirm:
                raise serializers.ValidationError(
                    {"password_confirm": "Las contraseñas no coinciden."}
                )
            if len(password) < 8:
                raise serializers.ValidationError(
                    {"password": "Mínimo 8 caracteres."}
                )
        else:
            attrs.pop("password", None)
        attrs.pop("password_confirm", None)
        return attrs


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = core_models.Branch
        exclude = ('created_by', 'updated_by')

    def validate_client(self, client):
        request = self.context.get("request")
        workspace = get_workspace_for_request(request)
        if workspace is None:
            raise serializers.ValidationError("Owner no resuelto.")
        if client.workspace_id != workspace.id:
            raise serializers.ValidationError("Cliente no pertenece a este owner.")
        return client


class ClientSerializer(serializers.ModelSerializer):
    branches = BranchSerializer(many=True, read_only=True)
    cavas = serializers.SerializerMethodField()
    tariff = serializers.SerializerMethodField()
    tariff_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    client_users = serializers.SerializerMethodField()

    class Meta:
        model = core_models.Client
        exclude = ('created_by', 'updated_by')
        read_only_fields = (
            'workspace',
            'random_id',
            'slug',
            'created_at',
            'updated_at',
        )

    def get_client_users(self, obj):
        users = obj.users.filter(user_type=choices.UserType.CLIENT).order_by("-date_joined")
        return CustomUserSerializer(users, many=True).data

    def get_cavas(self, obj):
        return [{
            'id': cava.id,
            'name': cava.name,
        } for cava in obj.cavas.all()]

    def get_tariff(self, obj):
        if obj.tariff:
            return {
                'id': obj.tariff.id,
                'name': obj.tariff.name,
            }
        return None

    def _resolve_tariff(self, tariff_id):
        if not tariff_id:
            return None
        from apps.finance.models import Tariff

        try:
            return Tariff.objects.get(id=tariff_id)
        except Tariff.DoesNotExist as exc:
            raise serializers.ValidationError({"tariff_id": "Tarifa no encontrada."}) from exc

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is None and not attrs.get("tariff_id"):
            raise serializers.ValidationError({"tariff_id": "La tarifa es obligatoria."})
        return attrs

    def create(self, validated_data):
        tariff_id = validated_data.pop("tariff_id", None)
        validated_data["tariff"] = self._resolve_tariff(tariff_id)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        tariff_id = validated_data.pop("tariff_id", None)
        if tariff_id is not None:
            instance.tariff = self._resolve_tariff(tariff_id) if tariff_id else None
        return super().update(instance, validated_data)
