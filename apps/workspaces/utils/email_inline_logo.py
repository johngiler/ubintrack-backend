"""
Logo del workspace en correos transaccionales.

Gmail y la mayoría de clientes muestran el adjunto inline (``cid:``). Apple Mail ignora
a menudo el CID pero renderiza ``<img src="data:…">``; si ambos se muestran a la vez
aparece el logo duplicado. Se usan dos ``<img>`` y CSS WebKit para mostrar solo uno.
"""

from __future__ import annotations

import base64
import html
from pathlib import Path

TENANT_TRANSACTIONAL_EMAIL_LOGO_CID = "tenant-email-logo"

_EMAIL_LOGO_DISPLAY_WIDTH_PX = 200

# Estilos en <head> del HTML transaccional (ver transactional_email_templates).
EMAIL_LOGO_HEAD_STYLES = """
<style type="text/css">
  img.logo-email-apple {
    display: none !important;
    max-height: 0 !important;
    overflow: hidden !important;
    width: 0 !important;
    height: 0 !important;
    mso-hide: all;
  }
  @media screen and (-webkit-min-device-pixel-ratio:0) {
    img.logo-email-cid {
      display: none !important;
      max-height: 0 !important;
      overflow: hidden !important;
      width: 0 !important;
      height: 0 !important;
    }
    img.logo-email-apple {
      display: block !important;
      width: 200px !important;
      height: auto !important;
      max-height: none !important;
      overflow: visible !important;
    }
  }
</style>
"""


def workspace_email_logo_inline_filename() -> str:
    """Nombre del adjunto inline; debe coincidir con ``src="cid:…"`` en el HTML."""
    return f"{TENANT_TRANSACTIONAL_EMAIL_LOGO_CID}.png"


def _png_pixel_size(raw: bytes) -> tuple[int, int] | None:
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width = int.from_bytes(raw[16:20], "big")
    height = int.from_bytes(raw[20:24], "big")
    if width < 1 or height < 1:
        return None
    return width, height


def _logo_display_dimensions(raw: bytes) -> tuple[int, int]:
    intrinsic = _png_pixel_size(raw)
    display_w = _EMAIL_LOGO_DISPLAY_WIDTH_PX
    if intrinsic:
        iw, ih = intrinsic
        return display_w, max(1, round(ih * display_w / iw))
    return display_w, 48


def workspace_email_logo_header_row(
    inline_logo: tuple[bytes, str, str] | None,
    *,
    alt: str,
) -> str:
    """Fila de cabecera con esquinas superiores redondeadas y logo (CID o data URI)."""
    safe_alt = html.escape((alt or "").strip() or "Marketplace", quote=True)
    cell_base = (
        "padding:28px 24px 12px;text-align:center;background-color:#fafafa;"
        "border-bottom:1px solid #f4f4f5;border-radius:16px 16px 0 0;"
    )

    if inline_logo is None:
        inner = (
            '<span style="font:700 18px/1.2 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
            f'color:#18181b;">{safe_alt}</span>'
        )
        return (
            f'<tr><td align="center" style="{cell_base}">{inner}</td></tr>'
        )

    raw, cid_name, mime = inline_logo
    if not raw:
        inner = (
            '<span style="font:700 18px/1.2 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
            f'color:#18181b;">{safe_alt}</span>'
        )
        return (
            f'<tr><td align="center" style="{cell_base}">{inner}</td></tr>'
        )

    display_w, display_h = _logo_display_dimensions(raw)
    b64 = base64.b64encode(raw).decode("ascii")
    mime_type = mime or "image/png"
    img_common = (
        f'width="{display_w}" height="{display_h}" alt="{safe_alt}" border="0" '
        f'style="max-width:100%;margin:0 auto;border:0;outline:none;text-decoration:none;'
        '-ms-interpolation-mode:bicubic;">'
    )
    cid_img = (
        f'<img src="cid:{cid_name}" class="logo-email-cid" {img_common}'
    )
    apple_img = (
        f'<img src="data:{mime_type};base64,{b64}" class="logo-email-apple" {img_common}'
    )
    inner = (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'align="center" style="margin:0 auto;"><tr><td align="center" '
        'style="padding:0;line-height:0;font-size:0;">'
        f"{cid_img}{apple_img}</td></tr></table>"
    )
    return (
        f'<tr><td align="center" style="{cell_base}">{inner}</td></tr>'
    )


def prepare_workspace_email_logo_for_inline(ws) -> tuple[bytes, str, str] | None:
    """Lee bytes del PNG del tenant (``logo_png_artifacts`` en Mi negocio)."""
    if ws is None:
        return None
    f = getattr(ws, "logo_png_artifacts", None)
    if not f or not getattr(f, "name", None):
        return None
    path = Path(str(f.name))
    if path.suffix.lower() != ".png":
        return None
    try:
        f.open("rb")
        try:
            raw = f.read()
        finally:
            f.close()
    except OSError:
        return None
    if not raw:
        return None
    return (raw, workspace_email_logo_inline_filename(), "image/png")
