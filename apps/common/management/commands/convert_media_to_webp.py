"""
Convierte imágenes existentes en MEDIA_ROOT a WebP y actualiza rutas en la base de datos.

Uso:
  python manage.py convert_media_to_webp
  python manage.py convert_media_to_webp --dry-run
  python manage.py convert_media_to_webp --scan-files  # también archivos sueltos bajo media/
"""

from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.ad_spaces.models import AdSpace, AdSpaceImage
from apps.clients.models import Client
from apps.common.utils.image_webp import DEFAULT_WEBP_QUALITY, ensure_imagefields_webp, raster_bytes_to_webp
from apps.malls.models import ShoppingCenter
from apps.users.models import UserProfile


RASTER_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif"}


class Command(BaseCommand):
    help = (
        "Convierte portadas, galería y fotos de usuario/empresa a WebP (modelos) "
        "y opcionalmente todos los raster en media/."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Solo muestra qué haría, sin escribir archivos ni BD.",
        )
        parser.add_argument(
            "--scan-files",
            action="store_true",
            help="Recorre MEDIA_ROOT y convierte .jpg/.png/.gif sueltos (actualiza BD si coincide la ruta).",
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

        n_repair = self._reconcile_stale_webp_paths(dry)
        if n_repair:
            self.stdout.write(self.style.SUCCESS(f"Rutas .webp reparadas (archivo raster en disco): {n_repair}"))

        n_models = self._convert_model_fields(dry, quality)
        self.stdout.write(self.style.SUCCESS(f"Filas de modelo procesadas: {n_models}"))

        if scan_files:
            n_fs = self._scan_filesystem(dry, quality)
            self.stdout.write(self.style.SUCCESS(f"Archivos en disco convertidos (o omitidos): {n_fs}"))

    def _convert_model_fields(self, dry: bool, quality: int) -> int:
        count = 0
        batches = [
            (
                ShoppingCenter.objects.exclude(cover_image="").exclude(cover_image__isnull=True),
                ("cover_image",),
            ),
            (
                AdSpace.objects.exclude(cover_image="").exclude(cover_image__isnull=True),
                ("cover_image",),
            ),
            (AdSpaceImage.objects.exclude(image=""), ("image",)),
            (
                UserProfile.objects.exclude(cover_image="").exclude(cover_image__isnull=True),
                ("cover_image",),
            ),
            (
                Client.objects.exclude(cover_image="").exclude(cover_image__isnull=True),
                ("cover_image",),
            ),
        ]

        for qs, fields in batches:
            for obj in qs.iterator():
                name = getattr(getattr(obj, fields[0]), "name", "") or ""
                if name.lower().endswith(".webp"):
                    continue
                self.stdout.write(f"  modelo {obj.__class__.__name__} pk={obj.pk} → {name}")
                if dry:
                    count += 1
                    continue
                with transaction.atomic():
                    ensure_imagefields_webp(obj, fields, quality=quality)
                    obj.save(update_fields=list(fields))
                count += 1
        return count

    def _scan_filesystem(self, dry: bool, quality: int) -> int:
        """Convierte raster en media/ que sigan existiendo como no-WebP (p. ej. huérfanos)."""
        from django.core.files.storage import default_storage

        media_root = getattr(settings, "MEDIA_ROOT", None)
        if not media_root or not os.path.isdir(media_root):
            self.stdout.write(self.style.WARNING("MEDIA_ROOT no configurado o no es directorio; --scan-files omitido."))
            return 0

        count = 0
        root = Path(media_root)
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in RASTER_SUFFIXES:
                continue
            if path.suffix.lower() == ".webp":
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
                self.stdout.write(self.style.WARNING(f"    omitido (no raster): {rel_old}"))
                continue

            webp_path.write_bytes(out)
            try:
                path.unlink()
            except OSError as e:
                self.stdout.write(self.style.WARNING(f"    no se pudo borrar original {rel_old}: {e}"))

            self._update_db_paths_if_needed(rel_old, rel_new)
            if default_storage.exists(rel_old) and rel_old != rel_new:
                try:
                    default_storage.delete(rel_old)
                except OSError:
                    pass
            count += 1
        return count

    def _reconcile_stale_webp_paths(self, dry: bool) -> int:
        """
        Si la BD apunta a .webp pero solo existe el raster (.png, .jpg, …),
        actualiza la ruta al archivo existente antes de convertir.
        """
        from django.core.files.storage import default_storage

        count = 0
        for model, field in (
            (UserProfile, "cover_image"),
            (Client, "cover_image"),
            (ShoppingCenter, "cover_image"),
            (AdSpace, "cover_image"),
            (AdSpaceImage, "image"),
        ):
            qs = model.objects.exclude(**{f"{field}": ""}).exclude(**{f"{field}__isnull": True})
            for obj in qs.iterator():
                name = getattr(getattr(obj, field), "name", "") or ""
                if not name.lower().endswith(".webp"):
                    continue
                if default_storage.exists(name):
                    continue
                stem = name[:-5]
                found = None
                for ext in (".png", ".jpg", ".jpeg", ".gif"):
                    alt = f"{stem}{ext}"
                    if default_storage.exists(alt):
                        found = alt
                        break
                if not found:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  sin archivo: {model.__name__} pk={obj.pk} → {name}"
                        )
                    )
                    continue
                self.stdout.write(f"  reparar {model.__name__} pk={obj.pk}: {name} → {found}")
                if not dry:
                    model.objects.filter(pk=obj.pk).update(**{field: found})
                count += 1
        return count

    def _update_db_paths_if_needed(self, rel_old: str, rel_new: str) -> None:
        """Si algún ImageField apuntaba al archivo antiguo, actualiza a .webp."""
        for model, field in (
            (ShoppingCenter, "cover_image"),
            (AdSpace, "cover_image"),
            (AdSpaceImage, "image"),
            (UserProfile, "cover_image"),
            (Client, "cover_image"),
        ):
            qs = model.objects.filter(**{f"{field}": rel_old})
            n = qs.update(**{field: rel_new})
            if n:
                self.stdout.write(f"    BD: actualizado {n} {model.__name__}(…{field})")
