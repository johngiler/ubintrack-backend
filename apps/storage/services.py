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
    """Prefijo de 2 caracteres para códigos de posición (p. ej. «Cava 1» → «C1»)."""
    parts = (name or "").split()
    if len(parts) >= 2:
        return f"{parts[0][0]}{parts[1][0]}".upper()
    s = (name or "XX").strip().upper()
    if len(s) >= 2:
        return s[:2]
    return s.ljust(2, "X")


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

    Sin nomenclatura, el resultado sigue siendo el de antes: C1-PA-R1-N1A.
    """
    if nomenclature is None:
        separator = "-"
        cava_prefix = ""
        rack_prefix = "R"
        level_prefix = "N"
    else:
        separator = nomenclature.separator or "-"
        cava_prefix = nomenclature.cava_prefix or ""
        rack_prefix = nomenclature.rack_prefix or ""
        level_prefix = nomenclature.level_prefix or ""

    token = str(cava_code or "").strip()
    if token:
        cava_segment = f"{cava_prefix}{token}"
    else:
        cava_segment = cava_code_prefix(cava_name)

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
    """
    Genera el código ZPL para una etiqueta de posición simplificada.
    Solo contiene NIVEL, QR y NOMENCLATURA.
    Centrado horizontalmente en toda la etiqueta.
    """
    # Obtener información principal
    code = position.code
    level = position.level

    # Dimensiones totales de la etiqueta (4x6 pulgadas a 203 DPI)
    total_width = 812   # 4 pulgadas a 203 DPI
    total_height = 1218  # 6 pulgadas a 203 DPI

    # Centro horizontal de la etiqueta
    center_x = total_width / 2

    # Posiciones verticales - ajustadas para que aparezcan en la parte superior
    # pero con suficiente espacio entre elementos
    top_margin = 40     # Margen superior
    title_y = top_margin
    qr_y = title_y + 60     # Espacio después del título
    code_y = qr_y + 240     # Espacio después del QR

    # Tamaños de elementos - estimaciones basadas en la imagen
    nivel_width = 92   # Ancho aproximado del texto "NIVEL X"
    code_width = 150    # Ancho aproximado del código
    qr_width = 88     # Ancho aproximado del QR

    # Calcular posiciones horizontales para centrado perfecto
    title_x = center_x - nivel_width
    qr_x = center_x - qr_width
    code_x = center_x - code_width

    # Configuración de la etiqueta
    zpl = f"""
^XA
^PW{total_width}    ; Ancho de la etiqueta (4 pulgadas)
^LL{total_height}   ; Largo de la etiqueta (6 pulgadas)
^CF0,45   ; Fuente por defecto y tamaño

; Título: NIVEL
^FO{title_x},{title_y}^A0N,55,55^FDNIVEL {level}^FS

; QR Code
^FO{qr_x},{qr_y}^BQN,1,8^FDMA,{code}^FS

; Código de posición
^FO{code_x},{code_y}^A0N,55,55^FD{code}^FS

^XZ
"""

    return zpl
