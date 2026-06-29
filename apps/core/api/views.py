from rest_framework import mixins, viewsets, filters
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, BasePermission
from utils.permissions_mixins import ClientScopeMixin, StrictDjangoModelPermissions, WorkspaceScopeMixin
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from django.db.models import Count
from rest_framework.pagination import PageNumberPagination
from django_filters.rest_framework import DjangoFilterBackend

from apps.core import models as core_models
from apps.core import choices as core_choices
from apps.core.user_roles import (
    can_manage_as_administrator,
    get_user_client,
    is_workspace_administrator,
)
from apps.workspaces.tenant import get_workspace_for_request, is_platform_staff, user_can_login_frontend

LOGIN_FAILED_MESSAGE = "Credenciales inválidas"
from . import serializers


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 1000


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

        return Response({"token": token.key, "user": user_serializer.data})


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
    pagination_class = StandardResultsSetPagination
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
        instance.delete()


class ProfileViewSet(WorkspaceScopeMixin, viewsets.ModelViewSet):
    queryset = core_models.Profile.objects.annotate(
        users_count=Count("custom_users")
    ).order_by("-created_at")
    serializer_class = serializers.ProfileSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["is_active", "user_type"]
    search_fields = ["name"]
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]

    def get_queryset(self):
        return self.scope_to_workspace(super().get_queryset())

    def perform_create(self, serializer):
        ws = get_workspace_for_request(self.request)
        if ws is None:
            raise ValidationError({"detail": "Owner no resuelto."})
        serializer.save(workspace=ws)


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
    pagination_class = StandardResultsSetPagination
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
        ws = get_workspace_for_request(self.request)
        if ws is None:
            raise ValidationError({"detail": "Owner no resuelto."})
        serializer.save(workspace=ws)


class BranchViewSet(WorkspaceScopeMixin, viewsets.ModelViewSet):
    queryset = core_models.Branch.objects.select_related("client").all().order_by('-created_at')
    serializer_class = serializers.BranchSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['client',]
    search_fields = ['name']
    permission_classes = [IsAuthenticated, StrictDjangoModelPermissions]
    workspace_field = "client__workspace"

    def get_queryset(self):
        return self.scope_to_workspace(super().get_queryset())


class WhoAmIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = serializers.build_whoami_payload(request.user, request)
        if data is None:
            return Response(status=403)
        return Response(data)


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
