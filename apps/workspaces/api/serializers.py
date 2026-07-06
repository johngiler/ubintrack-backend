from rest_framework import serializers

from apps.workspaces.models import Workspace


class WorkspacePublicSerializer(serializers.ModelSerializer):
    """Branding y metadatos públicos del owner resuelto por la petición."""

    logo_url = serializers.SerializerMethodField()
    logo_mark_url = serializers.SerializerMethodField()
    favicon_url = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = (
            "slug",
            "name",
            "legal_name",
            "app_title",
            "app_tagline",
            "primary_color",
            "secondary_color",
            "sidebar_logo_gradient_mode",
            "support_email",
            "phone",
            "logo_url",
            "logo_mark_url",
            "favicon_url",
        )

    def _absolute_media(self, obj, field_name: str) -> str | None:
        f = getattr(obj, field_name, None)
        if not f:
            return None
        request = self.context.get("request")
        url = f.url
        if request:
            uri = request.build_absolute_uri(url)
            if uri.startswith("http://") and request.META.get("HTTP_X_FORWARDED_PROTO", "").lower() == "https":
                return "https://" + uri[7:]
            return uri
        return url

    def get_logo_url(self, obj):
        return self._absolute_media(obj, "logo")

    def get_logo_mark_url(self, obj):
        return self._absolute_media(obj, "logo_mark")

    def get_favicon_url(self, obj):
        return self._absolute_media(obj, "favicon")


class WorkspaceMeReadSerializer(WorkspacePublicSerializer):
    """Configuración completa del owner para administradores (GET /api/me/workspace/)."""

    class Meta(WorkspacePublicSerializer.Meta):
        fields = WorkspacePublicSerializer.Meta.fields + (
            "rif",
            "invoice_prefix",
            "is_active",
        )


class WorkspaceMeUpdateSerializer(serializers.ModelSerializer):
    """Actualización por admin del owner. `slug` no es editable (define la URL)."""

    class Meta:
        model = Workspace
        fields = (
            "name",
            "legal_name",
            "rif",
            "primary_color",
            "secondary_color",
            "sidebar_logo_gradient_mode",
            "support_email",
            "phone",
            "app_title",
            "app_tagline",
            "invoice_prefix",
            "logo",
            "logo_mark",
            "favicon",
        )
        extra_kwargs = {
            "logo": {"required": False, "allow_null": True},
            "logo_mark": {"required": False, "allow_null": True},
            "favicon": {"required": False, "allow_null": True},
        }

    def validate(self, attrs):
        for field in ("logo", "logo_mark", "favicon"):
            if field in attrs and attrs[field] is None:
                attrs.pop(field)
        return attrs
