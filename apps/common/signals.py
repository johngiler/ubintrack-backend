"""Conversión automática a WebP antes de guardar modelos con imágenes raster."""

from __future__ import annotations

from django.db.models.signals import pre_save

from apps.common.utils.image_webp import ensure_imagefields_webp

_WEBP_SENDERS: dict[type, tuple[str, ...]] = {}


def _convert_images_to_webp(sender, instance, **kwargs):
    field_names = _WEBP_SENDERS.get(sender)
    if field_names:
        ensure_imagefields_webp(instance, field_names)


def connect_webp_signals() -> None:
    from django.apps import apps

    specs: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("workspaces.Workspace", ("logo", "logo_mark", "favicon")),
        ("order.EntryDetail", ("image",)),
        ("order.ExitDetail", ("image",)),
        ("inventory.Bin", ("tag_image",)),
        ("storage.Position", ("qr_code_image",)),
    )

    for label, field_names in specs:
        model = apps.get_model(label)
        _WEBP_SENDERS[model] = field_names
        pre_save.connect(
            _convert_images_to_webp,
            sender=model,
            dispatch_uid=f"common-webp-{label.replace('.', '-')}",
        )
