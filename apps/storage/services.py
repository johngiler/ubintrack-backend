import os
import qrcode
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


def generate_qr_with_label(data, text, level=None, font_path=None):
    """
    Genera una imagen QR con etiqueta.
    Implementación simplificada para reproducir exactamente la imagen de referencia.
    """
    # Configuración QR con parámetros específicos para que se vea igual a la imagen
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4
    )
    qr.add_data(data)
    qr.make(fit=True)

    # Crear imagen QR
    qr_image = qr.make_image(fill_color="black", back_color="white")
    qr_image = qr_image.convert('RGB')

    # Dimensiones fijas para que se vea igual a la referencia
    qr_width, qr_height = qr_image.size
    total_width = qr_width

    # Espacios fijos para título y código, basados en la imagen de referencia
    nivel_space = 35      # Espacio para "NIVEL X"
    code_space = 40   # Espacio para el código

    # Altura total con espacios exactos
    total_height = nivel_space + qr_height + code_space

    # Crear imagen en blanco
    combined_image = Image.new(
        "RGB", (total_width, total_height), (255, 255, 255))

    # Pegar el QR en la posición exacta
    combined_image.paste(qr_image, (0, nivel_space))

    draw = ImageDraw.Draw(combined_image)

    try:
        # Cargar fuentes, intentando priorizar Arial o cualquier fuente sans-serif
        font_path = font_path or os.path.join(settings.BASE_DIR, "arial.ttf")
        # Tamaños de fuente grandes para que sean similares a la imagen
        level_font = ImageFont.truetype(
            font_path, 40)  # Más grande para el nivel
        code_font = ImageFont.truetype(font_path, 40)   # Grande para el código
    except OSError:
        try:
            # Intentar con fuentes del sistema
            level_font = ImageFont.truetype("Arial", 40)
            code_font = ImageFont.truetype("Arial", 40)
        except OSError:
            try:
                # Otra alternativa común
                level_font = ImageFont.truetype("DejaVuSans", 40)
                code_font = ImageFont.truetype("DejaVuSans", 40)
            except OSError:
                logger.error(
                    "No se pudo cargar ninguna fuente. Usando la fuente predeterminada.")
                level_font = ImageFont.load_default()
                code_font = ImageFont.load_default()

    # Dibujar el nivel centrado en la parte superior
    if level is not None:
        level_text = f"NIVEL {level}"

        # Medir y centrar el texto
        try:
            level_bbox = draw.textbbox((0, 0), level_text, font=level_font)
            level_width = level_bbox[2] - level_bbox[0]
        except AttributeError:
            level_width = draw.textlength(level_text, font=level_font)

        # Posición exacta en el espacio asignado
        level_x = (total_width - level_width) // 2
        level_y = 15  # Ajustado específicamente para esta imagen

        # Efecto de negrita para que sea muy visible
        for dx, dy in [(0, 0), (1, 0), (0, 1), (1, 1)]:
            draw.text((level_x + dx, level_y + dy), level_text,
                      font=level_font, fill="black")

    # Dibujar el código centrado en la parte inferior
    try:
        code_bbox = draw.textbbox((0, 0), text, font=code_font)
        code_width = code_bbox[2] - code_bbox[0]
    except AttributeError:
        code_width = draw.textlength(text, font=code_font)

    # Posición exacta para el código
    code_x = (total_width - code_width) // 2
    code_y = qr_height + 10

    draw.text((code_x, code_y), text, font=code_font, fill="black")

    buffer = BytesIO()
    combined_image.save(buffer, format="WEBP", quality=85, method=6)
    buffer.seek(0)

    return buffer


def cava_code_prefix(name: str) -> str:
    """Token de cava desde el nombre (mismas reglas que pasillo).

    «Anden 1» → A1 · «Paola Cabrona» → PC · «Cava 1» → C1.
    """
    from apps.storage.utils.aisle_token import aisle_token_from_name

    return aisle_token_from_name(name) or "XX"


def generate_rack_list(number):
    return [(i, f'Rack {i}') for i in range(1, number + 1)]


def build_position_code(
    *,
    cava_name,
    cava_code,
    hall,
    rack,
    level,
    place,
    nomenclature=None,
):
    """Arma el código con la nomenclatura del owner.

    El token de cava ya viene completo (A1, PC, C1); no se antepone cava_prefix.
    """
    if nomenclature is None:
        separator = "-"
        rack_prefix = "R"
        level_prefix = "N"
    else:
        separator = nomenclature.separator or "-"
        rack_prefix = nomenclature.rack_prefix or ""
        level_prefix = nomenclature.level_prefix or ""

    token = str(cava_code or "").strip()
    # Solo dígitos = esquema legacy («Anden»+«1» → A1); no anteponer cava_prefix.
    if token.isdigit():
        import re

        name_token = cava_code_prefix(cava_name)
        if name_token and name_token != "XX" and not re.search(r"\d+$", name_token):
            token = f"{name_token}{token}"
        else:
            token = name_token if name_token and name_token != "XX" else token
    cava_segment = token or cava_code_prefix(cava_name)

    def segment(prefix, value):
        raw = str(value or "").strip()
        if not raw:
            return ""
        return f"{prefix}{raw}"

    # Hall ya trae el token completo (P1, TT, …); no se antepone aisle_prefix.
    aisle_segment = str(hall or "").strip()
    rack_raw = str(rack or "").strip()
    # Posiciones de piso: C9-P1-P2-01 (sin R ni N).
    if not rack_raw:
        place_segment = str(place or "").strip()
        parts = [cava_segment, aisle_segment, place_segment]
        return separator.join(part for part in parts if part)

    rack_segment = segment(rack_prefix, rack_raw)
    level_segment = segment(level_prefix, level)
    depth = str(place or "").strip()
    parts = [cava_segment, aisle_segment, rack_segment, f"{level_segment}{depth}"]
    return separator.join(part for part in parts if part)


def _zpl_safe(value) -> str:
    return str(value or "").replace("^", " ").replace("~", " ")


def _workspace_for_position(position):
    from apps.workspaces.utils.label_geometry import load_workspace_for_label

    ws_id = getattr(position, "workspace_id", None)
    if not ws_id:
        ws = getattr(position, "workspace", None)
        ws_id = getattr(ws, "pk", None) if ws is not None else None
    if not ws_id:
        cava = getattr(position, "cava", None)
        storage = getattr(cava, "storage", None) if cava is not None else None
        ws = getattr(storage, "workspace", None)
        ws_id = getattr(ws, "pk", None) or getattr(storage, "workspace_id", None)
    return load_workspace_for_label(workspace_id=ws_id)


def _position_label_font(size: int):
    """Fuente sans para arte de posición (tamaño en px = dots @ DPI etiqueta)."""
    size = max(10, int(size))
    candidates = [
        os.path.join(settings.BASE_DIR, "arial.ttf"),
        os.path.join(settings.BASE_DIR, "Arial.ttf"),
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "Arial",
        "DejaVuSans",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_bbox(draw, text: str, font):
    text = str(text or "")
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1], bbox[0], bbox[1]
    except AttributeError:
        tw = int(draw.textlength(text, font=font))
        th = int(getattr(font, "size", 20) or 20)
        return tw, th, 0, 0


def _draw_text_top_centered(draw, center_x: int, top_y: int, text: str, font, *, bold=True):
    """Dibuja texto con borde superior en top_y y centro horizontal en center_x."""
    text = str(text or "")
    tw, th, ox, oy = _text_bbox(draw, text, font)
    x = int(center_x - tw / 2 - ox)
    y = int(top_y - oy)
    offsets = ((0, 0), (1, 0), (0, 1), (1, 1)) if bold else ((0, 0),)
    for dx, dy in offsets:
        draw.text((x + dx, y + dy), text, font=font, fill=0)
    return th


def _render_position_cell_bitmap(code: str, level: str, cell_w: int, cell_h: int) -> Image.Image:
    """
    Arte en dots (1 px = 1 dot @ DPI del owner).
    4×2" → celdas 2×2"; título/QR/código con el mismo gap y el mismo eje X.
    """
    cell_w = max(64, int(cell_w))
    cell_h = max(64, int(cell_h))
    img = Image.new("L", (cell_w, cell_h), 255)
    draw = ImageDraw.Draw(img)

    # Márgenes ~5% de la celda 2×2 @ 203 DPI (406 dots → ~20).
    pad = max(12, int(min(cell_w, cell_h) * 0.05))
    gap = max(10, int(min(cell_w, cell_h) * 0.035))  # mismo aire arriba y abajo del QR

    title_size = max(28, min(58, int(cell_h * 0.11)))
    code_size = max(22, min(46, int(cell_h * 0.09)))
    title_font = _position_label_font(title_size)
    code_font = _position_label_font(code_size)

    nivel_text = f"NIVEL {level}"
    code_text = str(code or "")
    _, title_th, _, _ = _text_bbox(draw, nivel_text, title_font)
    _, code_th, _, _ = _text_bbox(draw, code_text, code_font)
    # Negrita suma ~1 px de tinta visual.
    title_th += 1
    code_th += 1

    qr_size = min(
        cell_w - 2 * pad,
        cell_h - 2 * pad - title_th - code_th - 2 * gap,
    )
    qr_size = max(96, qr_size)

    # Bloque real (bbox texto + gaps iguales + QR) centrado en la celda.
    block_h = title_th + gap + qr_size + gap + code_th
    y0 = max(pad, (cell_h - block_h) // 2)
    cx = cell_w // 2

    _draw_text_top_centered(draw, cx, y0, nivel_text, title_font, bold=True)

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=1,
    )
    qr.add_data(code_text)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("L")
    qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.NEAREST)
    qr_x = cx - qr_size // 2
    qr_y = y0 + title_th + gap
    img.paste(qr_img, (qr_x, qr_y))

    code_top = qr_y + qr_size + gap
    _draw_text_top_centered(draw, cx, code_top, code_text, code_font, bold=True)

    # Recentrar por tinta real (bbox geométrico deja el bloque ópticamente arriba).
    return _center_bitmap_ink(img.convert("1"))


def _center_bitmap_ink(img: Image.Image) -> Image.Image:
    """
    Centra tinta en X. En Y: un poco bajo el centro geométrico
    (reparte el margen libre ~40% arriba / 60% abajo) sin achicar el arte.
    """
    if img.mode != "1":
        img = img.convert("1")
    width, height = img.size
    pixels = img.load()

    top = None
    bottom = None
    left = width
    right = -1
    for y in range(height):
        row_has = False
        for x in range(width):
            if pixels[x, y] != 0:
                continue
            row_has = True
            if x < left:
                left = x
            if x > right:
                right = x
        if row_has:
            if top is None:
                top = y
            bottom = y

    if top is None or right < left:
        return img

    band_h = bottom - top + 1
    free_y = max(0, height - band_h)
    # Más margen arriba que abajo → el bloque baja sin reducir QR/textos.
    new_top = int(free_y * 0.62) if free_y else 0
    new_left = max(0, (width - (right - left + 1)) // 2)

    cropped = img.crop((left, top, right + 1, bottom + 1))
    out = Image.new("1", (width, height), 1)
    out.paste(cropped, (new_left, new_top))
    return out


def _bitmap_to_zpl_gfa(img: Image.Image, *, x: int, y: int) -> str:
    """PIL 1-bit → ^GFA (ASCII hex). Negro = bit 1."""
    if img.mode != "1":
        img = img.convert("1")
    width, height = img.size
    row_bytes = (width + 7) // 8
    pixels = img.load()
    raw = bytearray()
    for row in range(height):
        for byte_i in range(row_bytes):
            byte_val = 0
            for bit in range(8):
                px = byte_i * 8 + bit
                if px >= width:
                    continue
                # mode 1: 0 = negro
                if pixels[px, row] == 0:
                    byte_val |= 0x80 >> bit
            raw.append(byte_val)
    total = len(raw)
    hex_data = raw.hex().upper()
    return f"^FO{x},{y}^GFA,{total},{total},{row_bytes},{hex_data}^FS\n"


def _position_cell_zpl(
    code: str,
    level: str,
    *,
    cell_x: int,
    cell_y: int,
    cell_w: int,
    cell_h: int,
) -> str:
    """Celda a tamaño completo; posición vertical solo mueve el bloque, no lo escala."""
    bitmap = _render_position_cell_bitmap(code, level, cell_w, cell_h)
    return _bitmap_to_zpl_gfa(bitmap, x=cell_x, y=cell_y)


def generate_position_tag_zpl(position):
    """
    Una posición: primera celda (izq. / arriba); resto de la etiqueta en blanco.
    Grid según medida del owner (paso 2").
    """
    from apps.workspaces.utils.label_geometry import label_geometry, position_label_grid

    ws = _workspace_for_position(position)
    code = _zpl_safe(getattr(position, "code", None))
    level = _zpl_safe(getattr(position, "level", None))
    geo = label_geometry(ws)
    grid = position_label_grid(ws)
    pw, ll = int(geo["pw"]), int(geo["ll"])
    cell_w = pw // grid["cols"]
    cell_h = ll // grid["rows"]
    body = _position_cell_zpl(
        code,
        level,
        cell_x=0,
        cell_y=0,
        cell_w=cell_w,
        cell_h=cell_h,
    )
    return f"^XA\n^PW{pw}\n^LL{ll}\n^LH0,0\n{body}^XZ\n"


def generate_position_multi_tag_zpl(positions) -> str:
    """
    Etiqueta física del owner: rejilla cols×rows (paso 2" según medida).
    Celdas vacías si hay menos posiciones que el máximo.
    """
    from apps.workspaces.utils.label_geometry import label_geometry, position_label_grid

    raw = list(positions)
    if not raw:
        return ""

    ws = _workspace_for_position(raw[0])
    geo = label_geometry(ws)
    grid = position_label_grid(ws)
    cols, rows = grid["cols"], grid["rows"]
    max_n = grid["max_per_label"]
    items = raw[:max_n]
    pw, ll = int(geo["pw"]), int(geo["ll"])
    cell_w, cell_h = pw // cols, ll // rows
    blocks: list[str] = []

    for i, pos in enumerate(items):
        code = _zpl_safe(getattr(pos, "code", None))
        level = _zpl_safe(getattr(pos, "level", None))
        col, row = i % cols, i // cols
        blocks.append(
            _position_cell_zpl(
                code,
                level,
                cell_x=col * cell_w,
                cell_y=row * cell_h,
                cell_w=cell_w,
                cell_h=cell_h,
            )
        )

    return f"^XA\n^PW{pw}\n^LL{ll}\n^LH0,0\n{''.join(blocks)}^XZ\n"
