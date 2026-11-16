"""Branding por workspace (PDF, etiquetas, etc.)."""
from __future__ import annotations

import logging
import shutil
import subprocess
from io import BytesIO

logger = logging.getLogger(__name__)


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


def _svg_to_png_bytes(raw: bytes, *, width: int = 800) -> bytes | None:
    """Rasteriza SVG → PNG con rsvg-convert si está disponible."""
    rsvg = shutil.which("rsvg-convert")
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
        logger.exception("No se pudo rasterizar SVG del logo")
    return None


def _field_to_png_bytes(field) -> bytes | None:
    """Lee logo/isotipo y devuelve PNG (soporta SVG vía rsvg-convert)."""
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
    """Solo el Image del logo del workspace, o None si no hay."""
    from reportlab.lib.units import inch
    from reportlab.platypus import Image

    if workspace is None:
        return None

    for attr in ("logo", "logo_mark"):
        field = getattr(workspace, attr, None)
        png = _field_to_png_bytes(field)
        if not png:
            continue
        try:
            buf = BytesIO(png)
            img = Image(buf)
            img._restrictSize(size * inch, size * inch)
            return img
        except Exception:
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
