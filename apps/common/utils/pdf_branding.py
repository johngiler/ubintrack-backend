"""Branding por workspace (PDF, etiquetas, etc.)."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from io import BytesIO

from reportlab.lib.units import inch

logger = logging.getLogger(__name__)

# Ancho útil con letter + márgenes 0.75" (mismo que la tabla de productos).
ORDER_PDF_CONTENT_WIDTH = 7.0 * inch

# Bordes sutiles (cabecera + tablas del documento)
ORDER_PDF_BORDER = "#CBD5E1"
ORDER_PDF_BORDER_WIDTH = 0.5
ORDER_PDF_DIVIDER = "#E2E8F0"

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


def _pdf_escape(text: str) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def append_order_document_header(
    elements,
    workspace,
    *,
    doc_title: str,
    left_rows: list[tuple[str, str]],
    right_rows: list[tuple[str, str]] | None = None,
    texto_rows: list[tuple[str, str]] | None = None,
    subtitle: str = "GERENCIA DE OPERACIONES",
    content_width=None,
    logo_size: float = 1.05,
):
    """
    Cabecera de documento de orden:
    logo + títulos | ficha de datos (izq) + extras normal/check (der) | texto debajo.
    Todo al mismo ancho que la tabla de productos (content_width / doc.width).
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    if content_width is None:
        content_width = ORDER_PDF_CONTENT_WIDTH
    content_width = float(content_width)

    brand_label = ParagraphStyle(
        name="OrderPdfBrandLabel",
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#64748B"),
        alignment=TA_RIGHT,
        spaceAfter=2,
    )
    brand_title = ParagraphStyle(
        name="OrderPdfBrandTitle",
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=17,
        textColor=colors.HexColor("#0F172A"),
        alignment=TA_RIGHT,
        spaceAfter=0,
    )
    meta_label = ParagraphStyle(
        name="OrderPdfMetaLabel",
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#334155"),
        alignment=TA_LEFT,
    )
    meta_value = ParagraphStyle(
        name="OrderPdfMetaValue",
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#0F172A"),
        alignment=TA_LEFT,
    )
    section_label = ParagraphStyle(
        name="OrderPdfSectionLabel",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#334155"),
        alignment=TA_LEFT,
        spaceAfter=2,
    )
    texto_body = ParagraphStyle(
        name="OrderPdfTextoBody",
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#0F172A"),
        alignment=TA_LEFT,
    )

    def _key_para(label: str):
        return Paragraph(
            _pdf_escape(f"{str(label or '').upper()}:"),
            meta_label,
        )

    def _val_para(value: str):
        return Paragraph(_pdf_escape(value), meta_value)

    # Fila superior: logo (izq) + títulos (der)
    logo = workspace_logo_image(workspace, size=logo_size)
    if logo is None:
        logo = Paragraph(
            f"<b>{_pdf_escape(workspace_display_name(workspace))}</b>",
            ParagraphStyle(
                name="OrderPdfLogoFallback",
                fontName="Helvetica-Bold",
                fontSize=11,
                leading=13,
                textColor=colors.HexColor("#0F172A"),
                alignment=TA_LEFT,
            ),
        )
    elif hasattr(logo, "hAlign"):
        logo.hAlign = "LEFT"

    titles = [
        Paragraph(_pdf_escape(subtitle), brand_label),
        Paragraph(_pdf_escape(doc_title), brand_title),
    ]
    logo_w = min(1.7 * inch, content_width * 0.28)
    title_w = content_width - logo_w
    brand = Table([[logo, titles]], colWidths=[logo_w, title_w])
    brand.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    border = colors.HexColor(ORDER_PDF_BORDER)
    divider = colors.HexColor(ORDER_PDF_DIVIDER)
    bw = ORDER_PDF_BORDER_WIDTH

    elements.append(brand)
    elements.append(Spacer(1, 0.10 * inch))
    # Línea al mismo ancho que la tabla
    rule = Table([[""]], colWidths=[content_width])
    rule.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -1), bw, border),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    elements.append(rule)
    elements.append(Spacer(1, 0.12 * inch))

    # Ficha: UNA sola tabla al ancho del documento (mismo GRID que productos)
    right_rows = list(right_rows or [])
    left_rows = list(left_rows or [])
    if right_rows:
        n = max(len(left_rows), len(right_rows))
        data = []
        for i in range(n):
            lk, lv = left_rows[i] if i < len(left_rows) else ("", "")
            rk, rv = right_rows[i] if i < len(right_rows) else ("", "")
            data.append(
                [
                    _key_para(lk) if lk else "",
                    _val_para(lv) if lk else "",
                    _key_para(rk) if rk else "",
                    _val_para(rv) if rk else "",
                ]
            )
        col_widths = [
            content_width * 0.22,
            content_width * 0.30,
            content_width * 0.20,
            content_width * 0.28,
        ]
        card = Table(data, colWidths=col_widths)
        card.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                    ("BOX", (0, 0), (-1, -1), bw, border),
                    ("LINEBEFORE", (2, 0), (2, -1), bw, divider),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
    else:
        data = [[_key_para(k), _val_para(v)] for k, v in left_rows]
        col_widths = [content_width * 0.28, content_width * 0.72]
        card = Table(data, colWidths=col_widths)
        card.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                    ("BOX", (0, 0), (-1, -1), bw, border),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
    elements.append(card)

    for name, text_val in texto_rows or []:
        elements.append(Spacer(1, 0.10 * inch))
        safe = _pdf_escape(text_val).replace("\n", "<br/>")
        block = Table(
            [
                [
                    Paragraph(
                        _pdf_escape(f"{str(name or '').upper()}:"),
                        section_label,
                    )
                ],
                [Paragraph(safe, texto_body)],
            ],
            colWidths=[content_width],
        )
        block.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ("BOX", (0, 0), (-1, -1), bw, border),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        elements.append(block)

    elements.append(Spacer(1, 0.20 * inch))


def split_extra_fields_for_pdf(raw_extras):
    """
    Separa extra_fields en (right_rows, texto_rows).
    normal/check → cabecera derecha; texto → bloque debajo.
    """
    right_rows: list[tuple[str, str]] = []
    texto_rows: list[tuple[str, str]] = []
    if not isinstance(raw_extras, list):
        return right_rows, texto_rows

    for item in raw_extras:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        field_type = str(item.get("type") or "normal").strip().lower()
        if field_type in ("text", "number", "date", "valor"):
            field_type = "normal"
        if field_type in ("textarea",):
            field_type = "texto"
        if field_type in ("bool", "boolean"):
            field_type = "check"
        value = item.get("value")
        if field_type == "check":
            checked = value is True or str(value).strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
                "sí",
                "si",
            )
            right_rows.append((name, "Sí" if checked else "No"))
        elif field_type == "texto":
            text_val = str(value or "").strip()
            if text_val:
                texto_rows.append((name, text_val))
        else:
            text_val = str(value or "").strip()
            if text_val:
                right_rows.append((name, text_val[:60]))
    return right_rows, texto_rows
