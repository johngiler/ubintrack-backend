"""
Convierte imágenes existentes en MEDIA_ROOT a WebP y actualiza rutas en la base de datos.

Las subidas nuevas se convierten automáticamente vía ``apps.common.signals`` (pre_save).
Este comando sirve para backfill o reparación:

  python manage.py convert_media_to_webp
  python manage.py convert_media_to_webp --dry-run
  python manage.py convert_media_to_webp --scan-files
"""

from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.common.utils.image_webp import (
    DEFAULT_WEBP_QUALITY,
    RASTER_EXTENSIONS,
    ensure_imagefields_webp,
    raster_bytes_to_webp,
    should_convert_path_to_webp,
)
from apps.inventory.models import Bin
from apps.order.models import EntryDetail, ExitDetail
from apps.storage.models import Position
from apps.workspaces.models import Workspace


class Command(BaseCommand):
    help = "Convierte imágenes raster del WMS a WebP (modelos y opcionalmente archivos sueltos en media/)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Solo muestra qué haría, sin escribir archivos ni BD.",
        )
        parser.add_argument(
            "--scan-files",
            action="store_true",
            help="Recorre MEDIA_ROOT y convierte raster huérfanos bajo media/.",
        )
        parser.add_argument(
            "--quality",
            type=int,
            default=DEFAULT_WEBP_QUALITY,
            help=f"Calidad WebP (por defecto {DEFAULT_WEBP_QUALITY}).",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        scan_files = options["scan_files"]
        quality = options["quality"]

        n_models = self._convert_model_fields(dry, quality)
        self.stdout.write(self.style.SUCCESS(f"Filas de modelo procesadas: {n_models}"))

        if scan_files:
            n_fs = self._scan_filesystem(dry, quality)
            self.stdout.write(self.style.SUCCESS(f"Archivos en disco convertidos (u omitidos): {n_fs}"))

    def _convert_model_fields(self, dry: bool, quality: int) -> int:
        count = 0
        batches = [
            (
                Workspace.objects.exclude(logo="").exclude(logo__isnull=True),
                ("logo",),
            ),
            (
                Workspace.objects.exclude(logo_mark="").exclude(logo_mark__isnull=True),
                ("logo_mark",),
            ),
            (
                Workspace.objects.exclude(favicon="").exclude(favicon__isnull=True),
                ("favicon",),
            ),
            (EntryDetail.objects.exclude(image=""), ("image",)),
            (ExitDetail.objects.exclude(image=""), ("image",)),
            (Bin.objects.exclude(tag_image=""), ("tag_image",)),
            (Position.objects.exclude(qr_code_image=""), ("qr_code_image",)),
        ]

        for qs, fields in batches:
            for obj in qs.iterator():
                name = getattr(getattr(obj, fields[0]), "name", "") or ""
                if not should_convert_path_to_webp(name):
                    continue
                self.stdout.write(f"  {obj.__class__.__name__} pk={obj.pk} → {name}")
                if dry:
                    count += 1
                    continue
                with transaction.atomic():
                    ensure_imagefields_webp(obj, fields, quality=quality)
                    obj.save(update_fields=list(fields))
                count += 1
        return count

    def _scan_filesystem(self, dry: bool, quality: int) -> int:
        from django.core.files.storage import default_storage

        media_root = getattr(settings, "MEDIA_ROOT", None)
        if not media_root or not os.path.isdir(media_root):
            self.stdout.write(self.style.WARNING("MEDIA_ROOT no configurado; --scan-files omitido."))
            return 0

        count = 0
        root = Path(media_root)
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in RASTER_EXTENSIONS:
                continue

            webp_path = path.with_suffix(".webp")
            if webp_path.exists():
                continue

            rel_old = str(path.relative_to(root)).replace("\\", "/")
            rel_new = str(webp_path.relative_to(root)).replace("\\", "/")

            self.stdout.write(f"  disco: {rel_old} → {rel_new}")
            if dry:
                count += 1
                continue

            raw = path.read_bytes()
            out = raster_bytes_to_webp(raw, quality=quality)
            if not out:
                self.stdout.write(self.style.WARNING(f"    omitido: {rel_old}"))
                continue

            webp_path.write_bytes(out)
            try:
                path.unlink()
            except OSError as e:
                self.stdout.write(self.style.WARNING(f"    no se pudo borrar {rel_old}: {e}"))

            self._update_db_paths_if_needed(rel_old, rel_new)
            if default_storage.exists(rel_old) and rel_old != rel_new:
                try:
                    default_storage.delete(rel_old)
                except OSError:
                    pass
            count += 1
        return count

    def _update_db_paths_if_needed(self, rel_old: str, rel_new: str) -> None:
        for model, field in (
            (Workspace, "logo"),
            (Workspace, "logo_mark"),
            (Workspace, "favicon"),
            (EntryDetail, "image"),
            (ExitDetail, "image"),
            (Bin, "tag_image"),
            (Position, "qr_code_image"),
        ):
            n = model.objects.filter(**{field: rel_old}).update(**{field: rel_new})
            if n:
                self.stdout.write(f"    BD: {n} {model.__name__}.{field}")
