"""
WebP en servidor: subida y ``save()`` de portadas / galería (Pillow), más ``convert_media_to_webp``.
"""

from __future__ import annotations

import logging
import os
from io import BytesIO

logger = logging.getLogger(__name__)

DEFAULT_WEBP_QUALITY = 85


def raster_bytes_to_webp(raw: bytes, quality: int = DEFAULT_WEBP_QUALITY) -> bytes | None:
    """
    Devuelve bytes WebP o None si no es una imagen raster reconocible (p. ej. SVG).
    GIF animado: solo el primer fotograma.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(BytesIO(raw))
    except (UnidentifiedImageError, OSError):
        return None

    try:
        if getattr(img, "is_animated", False):
            img.seek(0)
    except OSError:
        return None

    mode = img.mode
    if mode == "P":
        img = img.convert("RGBA")
    elif mode in ("LA", "L"):
        img = img.convert("RGBA")
    elif mode == "RGBA":
        pass
    elif mode == "RGB":
        pass
    else:
        try:
            img = img.convert("RGB")
        except OSError:
            return None

    out = BytesIO()
    try:
        img.save(out, format="WEBP", quality=quality, method=6)
    except OSError as e:
        logger.warning("webp: no se pudo codificar WebP: %s", e)
        return None
    return out.getvalue()


def ensure_imagefields_webp(instance, field_names: tuple[str, ...], quality: int = DEFAULT_WEBP_QUALITY) -> None:
    """
    Convierte a WebP los ImageField indicados antes de ``save()``.
    Si ya son .webp o no hay archivo, no hace nada. Si la conversión falla, deja el archivo original.
    """
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    for fname in field_names:
        field = getattr(instance, fname, None)
        if not field:
            continue
        old_name = getattr(field, "name", None) or ""
        if not old_name or old_name.lower().endswith(".webp"):
            continue

        try:
            field.open("rb")
            raw = field.read()
        except OSError as e:
            logger.warning("webp: no se pudo leer %s: %s", old_name, e)
            continue
        finally:
            try:
                field.close()
            except OSError:
                pass

        out = raster_bytes_to_webp(raw, quality=quality)
        if not out:
            logger.info("webp: omitido (no raster o no soportado): %s", old_name)
            continue

        # Solo el **basename**: si se pasa la ruta completa, Django vuelve a aplicar ``upload_to`` y duplica carpetas.
        stem = os.path.splitext(os.path.basename(old_name.replace("\\", "/")))[0]
        new_basename = f"{stem}.webp"

        cf = ContentFile(out)
        try:
            getattr(instance, fname).save(new_basename, cf, save=False)
        except OSError as e:
            logger.warning("webp: no se pudo guardar %s: %s", new_basename, e)
            continue

        new_name = getattr(field, "name", None) or ""
        if old_name != new_name and default_storage.exists(old_name):
            try:
                default_storage.delete(old_name)
            except OSError as e:
                logger.warning("webp: no se pudo borrar original %s: %s", old_name, e)
