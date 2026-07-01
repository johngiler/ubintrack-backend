"""Branding de PDFs por workspace (logo, nombre, pie)."""
from io import BytesIO


def workspace_display_name(workspace):
    if workspace is None:
        return "WMS"
    if workspace.app_title:
        return workspace.app_title.strip()
    if workspace.legal_name:
        return workspace.legal_name.strip()
    return workspace.name


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
