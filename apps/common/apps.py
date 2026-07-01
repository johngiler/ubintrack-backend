from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"
    label = "common"
    verbose_name = "Modelos base"

    def ready(self):
        from apps.common.signals import connect_webp_signals

        connect_webp_signals()
