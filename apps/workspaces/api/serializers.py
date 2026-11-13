from rest_framework import serializers

from apps.workspaces.models import Workspace


class WorkspacePublicSerializer(serializers.ModelSerializer):
    """Branding y metadatos públicos del owner resuelto por la petición."""

    logo_url = serializers.SerializerMethodField()
    logo_mark_url = serializers.SerializerMethodField()
    favicon_url = serializers.SerializerMethodField()
    # Medida de etiqueta (sin IP): packing de impresión en el WMS.
    max_positions_per_label = serializers.SerializerMethodField()

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
            "label_width_in",
            "label_height_in",
            "max_positions_per_label",
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

    def get_max_positions_per_label(self, obj) -> int:
        from apps.workspaces.utils.label_geometry import max_positions_per_label

        return max_positions_per_label(obj)


PRINTER_SETTINGS_FIELDS = (
    "printer_ip",
    "printer_port",
    "label_width_in",
    "label_height_in",
    "printer_dpi",
    "printer_language",
)


class WorkspaceMeReadSerializer(WorkspacePublicSerializer):
    """Configuración completa del owner para administradores (GET /api/me/workspace/)."""

    class Meta(WorkspacePublicSerializer.Meta):
        fields = WorkspacePublicSerializer.Meta.fields + (
            "rif",
            "invoice_prefix",
            "is_active",
        ) + PRINTER_SETTINGS_FIELDS


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
        ) + PRINTER_SETTINGS_FIELDS
        extra_kwargs = {
            "logo": {"required": False, "allow_null": True},
            "logo_mark": {"required": False, "allow_null": True},
            "favicon": {"required": False, "allow_null": True},
            "printer_ip": {"required": False, "allow_blank": True},
            "printer_port": {"required": False},
            "label_width_in": {"required": False},
            "label_height_in": {"required": False},
            "printer_dpi": {"required": False},
            "printer_language": {"required": False},
        }

    def validate(self, attrs):
        for field in ("logo", "logo_mark", "favicon"):
            if field in attrs and attrs[field] is None:
                attrs.pop(field)
        language = attrs.get("printer_language")
        if language is not None and language != Workspace.PrinterLanguage.ZPL:
            raise serializers.ValidationError(
                {"printer_language": "Lenguaje no soportado."}
            )
        return attrs
