"""Branding por workspace (PDF, etiquetas, etc.)."""
from io import BytesIO


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


def open_workspace_logo_pil(workspace, max_width, max_height):
    """Abre logo del workspace como PIL.Image redimensionado, o None."""
    from PIL import Image as PILImage

    if workspace is None:
        return None
    for attr in ("logo", "logo_mark"):
        field = getattr(workspace, attr, None)
        if not field:
            continue
        try:
            with field.open("rb") as fh:
                img = PILImage.open(fh)
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

def workspace_logo_flowable(workspace, fallback_style, size=1.5):
    """Logo del workspace para ReportLab o Paragraph con nombre."""
    from reportlab.lib.units import inch
    from reportlab.platypus import Image, Paragraph

    if workspace is None:
        return Paragraph(f"<b>{workspace_display_name(None)}</b>", fallback_style)

    for attr in ("logo", "logo_mark"):
        field = getattr(workspace, attr, None)
        if not field:
            continue
        try:
            with field.open("rb") as fh:
                buf = BytesIO(fh.read())
            return Image(buf, width=size * inch, height=size * inch)
        except Exception:
            continue

    return Paragraph(f"<b>{workspace_display_name(workspace)}</b>", fallback_style)


def append_workspace_logo(elements, workspace, fallback_style, size=1.5):
    logo = workspace_logo_flowable(workspace, fallback_style, size=size)
    if hasattr(logo, "hAlign"):
        logo.hAlign = "CENTER"
    elements.append(logo)


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
