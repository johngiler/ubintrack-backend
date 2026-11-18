"""Branding por workspace (PDF, etiquetas, etc.)."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from io import BytesIO

logger = logging.getLogger(__name__)

_RSVG_CANDIDATES = (
    "/opt/homebrew/bin/rsvg-convert",
    "/usr/local/bin/rsvg-convert",
    "/usr/bin/rsvg-convert",
)


def workspace_display_name(workspace):
    if workspace is None:
        return "WMS"
    if workspace.app_title:
        return workspace.app_title.strip()
    if workspace.legal_name:
        return workspace.legal_name.strip()
    return workspace.name


def workspace_from_client(client):
    if client is None:
        return None
    return getattr(client, "workspace", None)


def _is_svg_bytes(raw: bytes) -> bool:
    head = raw[:400].lstrip()
    return head.startswith(b"<svg") or (
        head.startswith(b"<?xml") and b"<svg" in head
    )


def _find_rsvg_convert() -> str | None:
    """rsvg-convert: PATH + rutas habituales (runserver/systemd suelen omitir Homebrew)."""
    found = shutil.which("rsvg-convert")
    if found and os.path.isfile(found) and os.access(found, os.X_OK):
        return found
    for path in _RSVG_CANDIDATES:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _svg_to_png_via_rsvg(raw: bytes, *, width: int = 800) -> bytes | None:
    rsvg = _find_rsvg_convert()
    if not rsvg:
        return None
    try:
        proc = subprocess.run(
            [rsvg, "-w", str(width), "-f", "png"],
            input=raw,
            capture_output=True,
            check=False,
            timeout=20,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
        logger.warning(
            "rsvg-convert falló (code=%s): %s",
            proc.returncode,
            (proc.stderr or b"")[:200],
        )
    except Exception:
        logger.exception("No se pudo rasterizar SVG del logo con rsvg-convert")
    return None


def _svg_to_drawing(raw: bytes, max_side: float):
    """Fallback sin rsvg: Drawing ReportLab vía svglib."""
    try:
        from svglib.svglib import svg2rlg
    except ImportError:
        return None
    try:
        drawing = svg2rlg(BytesIO(raw))
    except Exception:
        logger.exception("svglib no pudo parsear el SVG del logo")
        return None
    if drawing is None:
        return None
    try:
        w = float(drawing.width or 1)
        h = float(drawing.height or 1)
        if w <= 0 or h <= 0:
            return None
        scale = min(max_side / w, max_side / h)
        if scale <= 0:
            return None
        drawing.width = w * scale
        drawing.height = h * scale
        drawing.scale(scale, scale)
        if hasattr(drawing, "hAlign"):
            drawing.hAlign = "CENTER"
        return drawing
    except Exception:
        logger.exception("No se pudo escalar el Drawing del logo SVG")
        return None


def _svg_to_png_bytes(raw: bytes, *, width: int = 800) -> bytes | None:
    """Rasteriza SVG → PNG (rsvg-convert) o None si no hay convertidor."""
    png = _svg_to_png_via_rsvg(raw, width=width)
    if png:
        return png

    # Último recurso: renderPM si svglib + backend raster están disponibles
    try:
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPM

        drawing = svg2rlg(BytesIO(raw))
        if drawing is None:
            return None
        return renderPM.drawToString(drawing, fmt="PNG")
    except Exception:
        return None


def _field_to_png_bytes(field) -> bytes | None:
    """Lee logo/isotipo y devuelve PNG (soporta SVG vía rsvg/svglib)."""
    if not field:
        return None
    try:
        with field.open("rb") as fh:
            raw = fh.read()
    except Exception:
        return None
    if not raw:
        return None

    if _is_svg_bytes(raw):
        return _svg_to_png_bytes(raw)

    try:
        from PIL import Image as PILImage

        img = PILImage.open(BytesIO(raw))
        img.load()
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return raw


def open_workspace_logo_pil(workspace, max_width, max_height):
    """Abre logo del workspace como PIL.Image redimensionado, o None."""
    from PIL import Image as PILImage

    if workspace is None:
        return None
    for attr in ("logo", "logo_mark"):
        field = getattr(workspace, attr, None)
        png = _field_to_png_bytes(field)
        if not png:
            continue
        try:
            img = PILImage.open(BytesIO(png))
            img.load()
            img = img.convert("RGBA")
            img.thumbnail((max_width, max_height), PILImage.Resampling.LANCZOS)
            return img
        except Exception:
            continue
    return None


def draw_workspace_brand_header_pil(tag, draw, workspace, tag_width, y, title_font, padding):
    """Pega logo centrado o dibuja nombre del owner; devuelve Y tras el encabezado."""
    logo = open_workspace_logo_pil(workspace, tag_width - padding * 2, 140)
    if logo is not None:
        x = (tag_width - logo.width) // 2
        tag.paste(logo, (x, y), logo if logo.mode == "RGBA" else None)
        return y + logo.height + 40

    company_name = workspace_display_name(workspace)
    try:
        title_bbox = draw.textbbox((0, 0), company_name, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
    except AttributeError:
        title_width = draw.textlength(company_name, font=title_font)

    draw.text(((tag_width - title_width) / 2, y), company_name, font=title_font, fill="black")
    return y + 180


def workspace_logo_image(workspace, size=1.5):
    """Solo el Image/Drawing del logo del workspace, o None si no hay."""
    from reportlab.lib.units import inch
    from reportlab.platypus import Image

    if workspace is None:
        return None

    max_side = float(size * inch)

    for attr in ("logo", "logo_mark"):
        field = getattr(workspace, attr, None)
        if not field:
            continue
        try:
            with field.open("rb") as fh:
                raw = fh.read()
        except Exception:
            continue
        if not raw:
            continue

        # SVG: PNG vía rsvg/renderPM, o Drawing vía svglib
        if _is_svg_bytes(raw):
            png = _svg_to_png_bytes(raw)
            if png:
                try:
                    buf = BytesIO(png)
                    img = Image(buf, mask="auto")
                    img._ubintrack_buf = buf  # evitar GC del buffer
                    img._restrictSize(max_side, max_side)
                    return img
                except Exception:
                    logger.exception("ReportLab no pudo cargar PNG del logo SVG")
            drawing = _svg_to_drawing(raw, max_side)
            if drawing is not None:
                return drawing
            continue

        # Raster (webp/png/jpeg/…)
        png = _field_to_png_bytes(field)
        if not png:
            continue
        try:
            buf = BytesIO(png)
            img = Image(buf, mask="auto")
            img._ubintrack_buf = buf
            img._restrictSize(max_side, max_side)
            return img
        except Exception:
            logger.exception("ReportLab no pudo cargar el logo raster")
            continue
    return None


def workspace_logo_flowable(workspace, fallback_style, size=1.5):
    """Logo del workspace para ReportLab, o Paragraph con nombre si no hay logo."""
    from reportlab.platypus import Paragraph

    logo = workspace_logo_image(workspace, size=size)
    if logo is not None:
        return logo
    return Paragraph(
        f"<b>{workspace_display_name(workspace)}</b>",
        fallback_style,
    )


def append_workspace_logo(elements, workspace, fallback_style, size=1.5):
    """
    Encabezado de marca: logo centrado arriba.
    Si hay logo, no se muestra el nombre del owner.
    """
    from reportlab.platypus import Paragraph

    logo = workspace_logo_image(workspace, size=size)
    if logo is not None:
        if hasattr(logo, "hAlign"):
            logo.hAlign = "CENTER"
        elements.append(logo)
        return True

    name = Paragraph(
        f"<b>{workspace_display_name(workspace)}</b>",
        fallback_style,
    )
    if hasattr(name, "hAlign"):
        name.hAlign = "CENTER"
    elements.append(name)
    return False


def workspace_pdf_footer_paragraphs(workspace, style):
    """Líneas de pie de página según datos del owner (sin defaults hardcodeados)."""
    from reportlab.platypus import Paragraph

    paragraphs = []
    if workspace is None:
        return paragraphs

    if workspace.app_tagline:
        paragraphs.append(Paragraph(workspace.app_tagline.strip(), style))
    if workspace.phone:
        paragraphs.append(Paragraph(f"Tel: {workspace.phone.strip()}", style))
    if workspace.support_email:
        email = workspace.support_email.strip()
        paragraphs.append(Paragraph(f"<u>{email}</u>", style))

    return paragraphs
