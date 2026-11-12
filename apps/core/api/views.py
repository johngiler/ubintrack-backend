from rest_framework import mixins, viewsets, filters
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, BasePermission
from apps.common.utils.permissions_mixins import ClientScopeMixin, StrictDjangoModelPermissions, WorkspaceScopeMixin
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from django.db.models import Count
from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from datetime import datetime

from apps.core import models as core_models
from apps.core import choices as core_choices
from apps.core.services import export_clients_excel
from apps.core.utils.user_roles import (
    can_manage_as_administrator,
    get_user_client,
    is_workspace_administrator,
)
from apps.workspaces.utils.tenant import get_workspace_for_request, is_platform_staff, user_can_login_frontend

LOGIN_FAILED_MESSAGE = "Credenciales inválidas"
from . import serializers


class CustomObtainAuthToken(ObtainAuthToken):
    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]

        if not user.is_active:
            raise ValidationError({"detail": LOGIN_FAILED_MESSAGE})

        ws = get_workspace_for_request(request)
        if ws is None or not user_can_login_frontend(user, ws):
            raise ValidationError({"detail": LOGIN_FAILED_MESSAGE})

        token, _created = Token.objects.get_or_create(user=user)
        user_serializer = serializers.CustomUserSerializer(user)

        try:
            from apps.audit.models import ActivitySection
            from apps.audit.services.view_log import log_unit

            display = (
                (getattr(user, "get_full_name", lambda: "")() or "").strip()
                or getattr(user, "username", None)
                or str(user.pk)
            )
            log_unit(
                workspace=ws,
                section=ActivitySection.USER,
                action="login",
                summary=f"{display} inició sesión",
                actor=user,
                resource_id=user.pk,
                resource_code=getattr(user, "username", "") or str(user.pk),
            )
        except Exception:
            pass

        return Response({"token": token.key, "user": user_serializer.data})


class LogoutView(APIView):
    """Invalida el token de la sesión actual."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=204)


class CanManageWorkspaceUsers(BasePermission):
    """Solo administrador del owner (o staff de plataforma) gestiona usuarios."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        if is_platform_staff(user) or can_manage_as_administrator(user):
            return True
        return False


class CustomUserViewSet(WorkspaceScopeMixin, viewsets.ModelViewSet):
    queryset = core_models.CustomUser.objects.select_related(
        "profile", "client", "workspace"
    ).order_by("-date_joined")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["user_type", "client", "is_active"]
    search_fields = ["email", "full_name"]
    permission_classes = [IsAuthenticated, CanManageWorkspaceUsers, StrictDjangoModelPermissions]
    workspace_field = "workspace"

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return serializers.CustomUserWriteSerializer
        return serializers.CustomUserSerializer

    def get_queryset(self):
        qs = self.scope_to_workspace(super().get_queryset())
        user = self.request.user
        if is_platform_staff(user) or is_workspace_administrator(user):
            staff_only = self.request.query_params.get("staff_only")
            if staff_only in ("1", "true", "True"):
                qs = qs.exclude(user_type=core_choices.UserType.CLIENT)
            return qs
        return qs.filter(pk=user.pk)

    def perform_create(self, serializer):
        from apps.audit.services.crud_hooks import SECTION, log_crud

        obj = serializer.save()
        code = getattr(obj, "username", None) or getattr(obj, "email", None) or str(obj.pk)
        log_crud(
            workspace=getattr(obj, "workspace", None),
            section=SECTION.USER,
            action="create",
            verb="creó el usuario",
            label=code,
            actor=self.request.user,
            resource_id=obj.pk,
            resource_code=code,
        )

    def perform_update(self, serializer):
        from apps.audit.services.crud_hooks import SECTION, log_crud

        obj = serializer.save()
        code = getattr(obj, "username", None) or getattr(obj, "email", None) or str(obj.pk)
        log_crud(
            workspace=getattr(obj, "workspace", None),
            section=SECTION.USER,
            action="update",
            verb="actualizó el usuario",
            label=code,
            actor=self.request.user,
            resource_id=obj.pk,
            resource_code=code,
        )

    def perform_destroy(self, instance):
        if instance.pk == self.request.user.pk:
            raise PermissionDenied("No puede eliminar su propio usuario.")
        if instance.user_type == core_choices.UserType.ADMINISTRATOR:
            ws_id = instance.workspace_id
            admin_count = core_models.CustomUser.objects.filter(
                workspace_id=ws_id,
                user_type=core_choices.UserType.ADMINISTRATOR,
                is_active=True,
            ).count()
            if admin_count <= 1:
                raise PermissionDenied("Debe existir al menos un administrador activo.")
        from apps.audit.services.crud_hooks import SECTION, log_crud

        ws = instance.workspace
        code = getattr(instance, "username", None) or getattr(instance, "email", None) or str(instance.pk)
        pk = instance.pk
        instance.delete()
        log_crud(
            workspace=ws,
            section=SECTION.USER,
            action="delete",
            verb="eliminó el usuario",
            label=code,
            actor=self.request.user,
            resource_id=pk,
            resource_code=code,
        )


class ProfileViewSet(WorkspaceScopeMixin, viewsets.ModelViewSet):
    queryset = core_models.Profile.objects.annotate(
        users_count=Count("custom_users")
    ).order_by("-created_at")
    serializer_class = serializers.ProfileSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["is_active", "user_type"]
    search_fields = ["name"]
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]

    def get_queryset(self):
        return self.scope_to_workspace(super().get_queryset())

    def perform_create(self, serializer):
        from apps.audit.services.crud_hooks import SECTION, log_crud

        ws = get_workspace_for_request(self.request)
        if ws is None:
            raise ValidationError({"detail": "Owner no resuelto."})
        obj = serializer.save(workspace=ws)
        log_crud(
            workspace=ws,
            section=SECTION.PROFILE,
            action="create",
            verb="creó el perfil",
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
            section=SECTION.PROFILE,
            action="update",
            verb="actualizó el perfil",
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
            section=SECTION.PROFILE,
            action="delete",
            verb="eliminó el perfil",
            label=name,
            actor=self.request.user,
            resource_id=pk,
            resource_code=name,
        )


class CustomPermissionViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = (
        core_models.CustomPermission.objects.prefetch_related(
            "django_permissions__content_type"
        )
        .all()
        .order_by("module", "name")
    )
    serializer_class = serializers.CustomPermissionSerializer
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]
    pagination_class = None


class ClientViewSet(ClientScopeMixin, viewsets.ModelViewSet):
    queryset = core_models.Client.objects.all().order_by("-created_at")
    serializer_class = serializers.ClientSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["id", "is_active", "tariff"]
    search_fields = ["name"]
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]
    workspace_field = "workspace"

    def get_queryset(self):
        qs = self.scope_to_user(super().get_queryset())
        sin_tarifa = self.request.query_params.get("sin_tarifa")
        if sin_tarifa in ("1", "true", "True"):
            qs = qs.filter(tariff__isnull=True)
        return qs

    def perform_create(self, serializer):
        from apps.audit.services.crud_hooks import SECTION, log_crud

        ws = get_workspace_for_request(self.request)
        if ws is None:
            raise ValidationError({"detail": "Owner no resuelto."})
        obj = serializer.save(workspace=ws)
        log_crud(
            workspace=ws,
            section=SECTION.CLIENT,
            action="create",
            verb="creó el cliente",
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
            section=SECTION.CLIENT,
            action="update",
            verb="actualizó el cliente",
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
            section=SECTION.CLIENT,
            action="delete",
            verb="eliminó el cliente",
            label=name,
            actor=self.request.user,
            resource_id=pk,
            resource_code=name,
        )

    @action(detail=False, methods=['get'])
    def export_excel(self, request):
        """Exporta clientes a Excel respetando filtros y búsqueda de la lista."""
        queryset = self.filter_queryset(self.get_queryset())
        output = export_clients_excel(queryset)
        filename = f"Clientes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xls"
        response = HttpResponse(
            output.getvalue(),
            content_type='application/vnd.ms-excel',
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class BranchViewSet(WorkspaceScopeMixin, viewsets.ModelViewSet):
    queryset = core_models.Branch.objects.select_related("client").all().order_by('-created_at')
    serializer_class = serializers.BranchSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['client',]
    search_fields = ['name']
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]
    workspace_field = "client__workspace"

    def get_queryset(self):
        return self.scope_to_workspace(super().get_queryset())


class MeProfileView(APIView):
    """Perfil del usuario autenticado (lectura y actualización de datos propios)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = serializers.build_whoami_payload(request.user, request)
        if data is None:
            return Response(status=403)
        return Response(data)

    def patch(self, request):
        user = request.user
        payload = serializers.build_whoami_payload(user, request)
        if payload is None:
            return Response(status=403)

        body = serializers.MeProfileUpdateSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        validated = body.validated_data

        if "full_name" in validated:
            user.full_name = validated["full_name"]
        if "email" in validated:
            user.email = validated["email"]
            user.username = validated["email"]
        if "password" in validated:
            user.set_password(validated["password"])

        user.save()
        return Response(serializers.build_whoami_payload(user, request))
