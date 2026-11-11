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


def generate_position_tag_zpl(position):
    """ZPL de posición para etiqueta 4×2" @ 203 DPI (texto izq. + QR der., casi a sangre)."""
    code = str(position.code or "").replace("^", " ").replace("~", " ")
    level = str(position.level or "").replace("^", " ").replace("~", " ")

    # 4" × 2" @ 203 DPI
    return f"""^XA
^PW812
^LL406
^LH0,0
^FO16,50^A0N,70,70^FDNIVEL {level}^FS
^FO16,160^A0N,42,42^FD{code}^FS
^FO500,28^BQN,2,7^FDMA,{code}^FS
^XZ
"""
