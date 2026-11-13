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


def _position_zpl_landscape(code: str, level: str, geo: dict) -> str:
    """4×2 (u horizontal): texto izq. + QR der., escalado a pw×ll del owner."""
    from apps.workspaces.utils.label_geometry import scale_dot

    sx, sy = geo["sx"], geo["sy"]
    pw, ll = geo["pw"], geo["ll"]
    return (
        f"^XA\n"
        f"^PW{pw}\n"
        f"^LL{ll}\n"
        f"^LH0,0\n"
        f"^FO{scale_dot(16, sx)},{scale_dot(50, sy)}"
        f"^A0N,{scale_dot(70, sy)},{scale_dot(70, sx)}^FDNIVEL {level}^FS\n"
        f"^FO{scale_dot(16, sx)},{scale_dot(160, sy)}"
        f"^A0N,{scale_dot(42, sy)},{scale_dot(42, sx)}^FD{code}^FS\n"
        f"^FO{scale_dot(500, sx)},{scale_dot(28, sy)}"
        f"^BQN,2,{max(2, scale_dot(7, min(sx, sy)))}^FDMA,{code}^FS\n"
        f"^XZ\n"
    )


def _position_zpl_portrait(code: str, level: str, geo: dict) -> str:
    """Etiqueta más alta que ancha: NIVEL → QR → código (centrado)."""
    pw, ll = geo["pw"], geo["ll"]
    margin = max(12, min(pw, ll) // 20)
    title_h = max(28, min(72, ll // 12))
    code_h = max(22, min(56, ll // 14))
    title_y = margin
    usable = max(40, min(pw, ll) - margin * 2)
    target_qr = int(usable * 0.55)
    qr_mag = max(3, min(12, target_qr // 25))
    qr_size = qr_mag * 25
    qr_x = max(0, (pw - qr_size) // 2)
    qr_y = title_y + title_h + margin
    code_y = qr_y + qr_size + margin
    if code_y + code_h + margin > ll:
        code_y = max(qr_y + qr_size // 2, ll - code_h - margin)
        qr_y = max(title_y + title_h + margin // 2, code_y - qr_size - margin)
        qr_y = max(title_y + title_h, qr_y)

    nivel_text = f"NIVEL {level}"
    return (
        f"^XA\n"
        f"^PW{pw}\n"
        f"^LL{ll}\n"
        f"^LH0,0\n"
        f"^FO0,{title_y}^A0N,{title_h},{title_h}^FB{pw},1,0,C^FD{nivel_text}^FS\n"
        f"^FO{qr_x},{qr_y}^BQN,2,{qr_mag}^FDMA,{code}^FS\n"
        f"^FO0,{code_y}^A0N,{code_h},{code_h}^FB{pw},1,0,C^FD{code}^FS\n"
        f"^XZ\n"
    )


def generate_position_tag_zpl(position):
    """
    ZPL de posición según medida/DPI del owner (Configuración del propietario).
    Landscape (p. ej. 4×2): texto izq. + QR der.
    Portrait: NIVEL → QR → código centrados.
    """
    from apps.workspaces.utils.label_geometry import label_geometry

    code = _zpl_safe(getattr(position, "code", None))
    level = _zpl_safe(getattr(position, "level", None))
    geo = label_geometry(_workspace_for_position(position))
    if geo["landscape"]:
        return _position_zpl_landscape(code, level, geo)
    return _position_zpl_portrait(code, level, geo)


def generate_position_multi_tag_zpl(positions) -> str:
    """
    Una etiqueta física con varias posiciones distintas (escala por owner).
    1 → layout actual; 2–3 en fila; 4 en rejilla 2×2.
    """
    from apps.workspaces.utils.label_geometry import label_geometry, scale_dot

    items = list(positions)[:4]
    if not items:
        return ""
    if len(items) == 1:
        return generate_position_tag_zpl(items[0])

    geo = label_geometry(_workspace_for_position(items[0]))
    sx, sy = geo["sx"], geo["sy"]
    pw, ll = geo["pw"], geo["ll"]
    n = len(items)
    blocks: list[str] = []

    if n <= 3:
        cell_w = pw // n
        qr_mag = max(2, scale_dot(5 if n == 2 else 4, min(sx, sy)))
        for i, pos in enumerate(items):
            code = _zpl_safe(getattr(pos, "code", None))
            level = _zpl_safe(getattr(pos, "level", None))
            x = i * cell_w
            qr_x = x + max(scale_dot(8, sx), (cell_w - (qr_mag * 25)) // 2)
            blocks.append(
                f"^FO{x + scale_dot(8, sx)},{scale_dot(18, sy)}"
                f"^A0N,{scale_dot(28, sy)},{scale_dot(28, sx)}^FDNIVEL {level}^FS\n"
                f"^FO{x + scale_dot(8, sx)},{scale_dot(56, sy)}"
                f"^A0N,{scale_dot(22, sy)},{scale_dot(22, sx)}^FD{code}^FS\n"
                f"^FO{qr_x},{scale_dot(100, sy)}^BQN,2,{qr_mag}^FDMA,{code}^FS"
            )
    else:
        cell_w, cell_h = pw // 2, ll // 2
        qr_mag = max(2, scale_dot(4, min(sx, sy)))
        for i, pos in enumerate(items):
            code = _zpl_safe(getattr(pos, "code", None))
            level = _zpl_safe(getattr(pos, "level", None))
            col, row = i % 2, i // 2
            x, y = col * cell_w, row * cell_h
            qr_x = x + max(scale_dot(8, sx), (cell_w - (qr_mag * 25)) // 2)
            blocks.append(
                f"^FO{x + scale_dot(8, sx)},{y + scale_dot(10, sy)}"
                f"^A0N,{scale_dot(24, sy)},{scale_dot(24, sx)}^FDNIVEL {level}^FS\n"
                f"^FO{x + scale_dot(8, sx)},{y + scale_dot(40, sy)}"
                f"^A0N,{scale_dot(20, sy)},{scale_dot(20, sx)}^FD{code}^FS\n"
                f"^FO{qr_x},{y + scale_dot(70, sy)}^BQN,2,{qr_mag}^FDMA,{code}^FS"
            )

    body = "\n".join(blocks)
    return f"^XA\n^PW{pw}\n^LL{ll}\n^LH0,0\n{body}\n^XZ\n"
