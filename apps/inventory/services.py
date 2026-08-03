import os
import qrcode
import xlwt
from io import BytesIO
import logging
from PIL import Image, ImageDraw, ImageFont
from django.conf import settings
from django.db.models import Sum

from . import models
from apps.order.models import ExitProduct
from apps.core.choices import OrderStatus, BinStatus
from apps.common.utils.media_layout import build_wms_bin_tag_relative, ensure_media_parent_dir
from apps.common.utils.pdf_branding import (
    draw_workspace_brand_header_pil,
    workspace_display_name,
    workspace_from_client,
)

logger = logging.getLogger(__name__)


def generate_bin_tag(bin_instance):
    """
    Generates a tag for a bin with relevant information.
    Returns the full path where the generated tag is saved.
    """

    # Configuración de dimensiones de la etiqueta (4x6 pulgadas a 300 DPI) - VERTICAL
    tag_width = 1200   # 4 pulgadas x 300 DPI
    tag_height = 1800  # 6 pulgadas x 300 DPI
    padding = 60       # Margen aumentado

    # Crear la imagen del tag con fondo blanco
    tag = Image.new("RGB", (tag_width, tag_height), (255, 255, 255))
    draw = ImageDraw.Draw(tag)

    # Cargar fuentes con tamaños adecuados
    try:
        font_path = os.path.join(settings.BASE_DIR, "arial.ttf")
        title_font = ImageFont.truetype(font_path, 60)     # Título
        header_font = ImageFont.truetype(font_path, 32)    # Etiquetas
        text_font = ImageFont.truetype(font_path, 28)      # Valores
    except OSError:
        # Si no se encuentra la fuente, usar una alternativa
        try:
            # Intentar con una fuente del sistema
            title_font = ImageFont.truetype("Arial", 60)
            header_font = ImageFont.truetype("Arial", 32)
            text_font = ImageFont.truetype("Arial", 28)
        except OSError:
            logger.error(
                "No se pudo cargar ninguna fuente. Usando la fuente predeterminada.")
            title_font = ImageFont.load_default()
            header_font = ImageFont.load_default()
            text_font = ImageFont.load_default()

    # Información principal
    client = bin_instance.client.name if bin_instance.client else "-"
    position = "-"

    # Incluir posición si está asignada
    if hasattr(bin_instance, 'position') and bin_instance.position:
        position = bin_instance.position.code

    bin_code = bin_instance.code
    workspace = workspace_from_client(bin_instance.client)

    y_pos = draw_workspace_brand_header_pil(
        tag, draw, workspace, tag_width, padding, title_font, padding,
    )

    # Diseño vertical con etiquetas y valores
    col1 = padding
    label_width = 250  # Espacio para separar keys y values
    value_max_width = tag_width - col1 - label_width - \
        padding * 2  # Ancho máximo para valores

    # Efecto de negrita para las etiquetas (dibujando el texto varias veces con pequeños desplazamientos)
    def draw_bold_text(x, y, text, font, fill="black"):
        # Dibujar el texto varias veces con pequeños desplazamientos para simular negrita
        for dx, dy in [(0, 0), (1, 0), (0, 1), (1, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=fill)

    # Función para hacer wrap de texto largo
    def wrap_text(text, font, max_width):
        words = text.split()
        lines = []
        current_line = []

        for word in words:
            # Probar si añadir esta palabra excede el ancho máximo
            test_line = ' '.join(current_line + [word])
            try:
                # Para versiones más recientes de PIL
                test_bbox = draw.textbbox((0, 0), test_line, font=font)
                test_width = test_bbox[2] - test_bbox[0]
            except AttributeError:
                # Para versiones antiguas de PIL
                test_width = draw.textlength(test_line, font=font)

            if test_width <= max_width:
                current_line.append(word)
            else:
                # Si la línea actual no está vacía, añadirla a las líneas
                if current_line:
                    lines.append(' '.join(current_line))
                    current_line = [word]
                else:
                    # Si la palabra es más larga que el ancho máximo, truncarla
                    truncated = truncate_text(word, font, max_width)
                    lines.append(truncated)
                    current_line = []

        # Añadir la última línea si no está vacía
        if current_line:
            lines.append(' '.join(current_line))

        return lines

    # Función para truncar texto que es demasiado largo incluso para una línea
    def truncate_text(text, font, max_width):
        if not text:
            return ""

        for i in range(len(text), 0, -1):
            truncated = text[:i] + "..." if i < len(text) else text
            try:
                # Para versiones más recientes de PIL
                test_bbox = draw.textbbox((0, 0), truncated, font=font)
                test_width = test_bbox[2] - test_bbox[0]
            except AttributeError:
                # Para versiones antiguas de PIL
                test_width = draw.textlength(truncated, font=font)

            if test_width <= max_width:
                return truncated

        return "..."  # Si incluso un carácter es demasiado ancho

    # Primera fila: Cliente
    # Mayúsculas y "negrita"
    draw_bold_text(col1, y_pos, "CLIENTE:", header_font)

    # Wrap para el cliente
    client_lines = wrap_text(client, text_font, value_max_width)
    client_y = y_pos
    for line in client_lines:
        draw.text((col1 + label_width, client_y),
                  line, font=text_font, fill="black")
        client_y += 40  # Espacio entre líneas de texto

    # Actualizar y_pos basado en cuántas líneas de cliente se mostraron
    # Al menos 100px de espacio o 60px después de la última línea
    y_pos = max(y_pos + 100, client_y + 60)

    # Segunda fila: Posición (solo para Bin)
    if hasattr(bin_instance, 'position'):
        # Mayúsculas y "negrita"
        draw_bold_text(col1, y_pos, "POSICIÓN:", header_font)
        draw.text((col1 + label_width, y_pos), position,
                  font=text_font, fill="black")
        y_pos += 100  # Espacio entre líneas

    # Tercera fila: BIN
    draw_bold_text(col1, y_pos, "BIN:", header_font)  # Mayúsculas y "negrita"
    draw.text((col1 + label_width, y_pos), bin_code,
              font=text_font, fill="black")

    # Espacio después de la información principal
    y_pos += 100  # Espacio entre secciones

    # Obtener los productos según el tipo de bin
    if isinstance(bin_instance, models.Bin):
        if hasattr(bin_instance, 'bin_products') and bin_instance.bin_products.exists():
            bin_product = bin_instance.bin_products.first()
            product = bin_product.entry_product.product
            product_info = {
                "Producto": product.name,
                "SKU": product.client_sku or "-",
                "Lote": bin_product.batch or bin_product.entry_product.batch or "-",
                "Cantidad": f"{bin_product.quantity} {product.uom}",
                "Fecha Exp.": bin_product.expiration_date.strftime("%Y-%m-%d") if bin_product.expiration_date else
                (bin_product.entry_product.expiration_date.strftime("%Y-%m-%d")
                 if bin_product.entry_product.expiration_date else "-")
            }

            # Mostrar información del producto en formato vertical (para Bin)
            if product_info:
                # Mostrar todos los campos con buen espaciado
                fields = ["Producto", "SKU", "Lote", "Cantidad", "Fecha Exp."]

                for field in fields:
                    value = product_info[field]

                    # Dibujar etiqueta (en mayúsculas y "negrita")
                    field_label = field.upper() + ":"
                    draw_bold_text(col1, y_pos, field_label, header_font)

                    # Wrap para el valor
                    value_lines = wrap_text(
                        str(value), text_font, value_max_width)
                    value_y = y_pos
                    for line in value_lines:
                        draw.text((col1 + label_width, value_y),
                                  line, font=text_font, fill="black")
                        value_y += 40  # Espacio entre líneas de texto

                    # Actualizar y_pos basado en cuántas líneas de valor se mostraron
                    # Al menos 100px de espacio o 60px después de la última línea
                    y_pos = max(y_pos + 100, value_y + 60)

    # Espacio antes del QR
    y_pos += 80  # Espacio antes del QR

    # Generar el QR solamente con el código del bin
    qr_data = bin_code

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4
    )
    qr.add_data(qr_data)
    qr.make(fit=True)

    # Convertir el QR a imagen - TAMAÑO GRANDE
    qr_image = qr.make_image(fill_color="black", back_color="white")
    qr_image = qr_image.convert('RGB')
    qr_image = qr_image.resize((450, 450))  # QR grande

    # Pegar el QR centrado al final
    qr_x = (tag_width - 450) // 2
    tag.paste(qr_image, (qr_x, y_pos))

    relative_path = build_wms_bin_tag_relative(bin_instance)
    tag_path = ensure_media_parent_dir(relative_path)
    tag.save(tag_path, format="WEBP", quality=85, method=6)

    return tag_path


def generate_bin_tag_zpl(bin_instance):
    """
    Genera el código ZPL para una etiqueta de bin.
    Retorna el string con el código ZPL.
    """
    # Obtener información principal
    def truncate_text_zpl(text, max_length=25):
        if len(text) > max_length:
            return text[:max_length - 3] + "..."
        return text

    def zpl_safe(text):
        return str(text).replace("^", " ").replace("~", " ")

    client = bin_instance.client.name if bin_instance.client else "-"
    position = "-"

    if bin_instance.position:
        position = bin_instance.position.code

    bin_code = bin_instance.code
    workspace = workspace_from_client(bin_instance.client)
    company_name = truncate_text_zpl(
        zpl_safe(workspace_display_name(workspace)))
    client = truncate_text_zpl(zpl_safe(client))
    position = zpl_safe(position)
    bin_code = zpl_safe(bin_code)

    # Reducir el espaciado vertical entre filas
    row_spacing = 70  # Reducido de 100 a 70

    # Ajustar las posiciones verticales iniciales
    title_y = 50      # Posición Y del título (sin cambios)
    title_bottom_margin = 100  # Margen inferior del título (aumentado)

    # Ajustado para dar más espacio después del título
    client_y = title_y + title_bottom_margin

    # Configuración de la etiqueta (4x6 pulgadas a 203 DPI)
    zpl = f"""
^XA
^PW812    ; Ancho de la etiqueta (4 pulgadas)
^LL1218   ; Largo de la etiqueta (6 pulgadas)
^CF0,45   ; Fuente por defecto y tamaño

; Título alineado a la izquierda con más margen inferior
^FO50,{title_y}^A0N,55,55^FD{company_name}^FS

; Cliente
^FO50,{client_y}^A0N,35,35^FDCLIENTE:^FS
^FO250,{client_y}^A0N,35,35^FD{client}^FS
"""

    y_pos = client_y + row_spacing

    zpl += f"""
; Posicion (sin tilde)
^FO50,{y_pos}^A0N,35,35^FDPOSICION:^FS
^FO250,{y_pos}^A0N,35,35^FD{position}^FS
"""
    y_pos += row_spacing

    # BIN
    zpl += f"""
; BIN
^FO50,{y_pos}^A0N,35,35^FDBIN:^FS
^FO250,{y_pos}^A0N,35,35^FD{bin_code}^FS
"""
    y_pos += row_spacing

    # Obtener producto(s) según el tipo de bin
    if isinstance(bin_instance, models.Bin):
        if hasattr(bin_instance, 'bin_products') and bin_instance.bin_products.exists():
            bin_product = bin_instance.bin_products.first()
            product = bin_product.entry_product.product
            product_info = {
                "Producto": truncate_text_zpl(product.name, 30),
                "SKU": truncate_text_zpl(product.client_sku or "-", 20),
                "Lote": truncate_text_zpl(bin_product.batch or bin_product.entry_product.batch or "-", 20),
                "Cantidad": truncate_text_zpl(f"{bin_product.quantity} {product.uom}", 15),
                "Fecha Exp.": bin_product.expiration_date.strftime("%Y-%m-%d") if bin_product.expiration_date else
                (bin_product.entry_product.expiration_date.strftime("%Y-%m-%d")
                 if bin_product.entry_product.expiration_date else "-")
            }

            # Añadir información del producto en formato vertical (para Bin)
            if product_info:
                fields = ["Producto", "SKU", "Lote", "Cantidad", "Fecha Exp."]
                for field in fields:
                    zpl += f"""
^FO50,{y_pos}^A0N,35,35^FD{field.upper()}:^FS
^FO250,{y_pos}^A0N,35,35^FD{product_info[field]}^FS
"""
                    y_pos += row_spacing

    # Generar datos para el QR
    qr_data = bin_code

    # Añadir QR centrado con tamaño reducido
    zpl += f"""
; QR Code centrado (tamaño reducido)
^FO231,{y_pos+30}^BQN,1,8^FDMA,{qr_data}^FS
^XZ
"""

    return zpl


def export_bin_entries_excel(bins_queryset):
    """
    Exports bin entries to Excel file.

    Args:
        bins_queryset: QuerySet of Bin objects

    Returns:
        BytesIO: Excel file buffer
    """
    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Bins')

    # Styles
    header_style = xlwt.XFStyle()
    header_font = xlwt.Font()
    header_font.bold = True
    header_font.colour_index = xlwt.Style.colour_map['white']
    header_style.font = header_font

    header_pattern = xlwt.Pattern()
    header_pattern.pattern = xlwt.Pattern.SOLID_PATTERN
    header_pattern.pattern_fore_colour = xlwt.Style.colour_map['dark_blue']
    header_style.pattern = header_pattern

    header_alignment = xlwt.Alignment()
    header_alignment.horz = xlwt.Alignment.HORZ_CENTER
    header_alignment.vert = xlwt.Alignment.VERT_CENTER
    header_style.alignment = header_alignment

    data_style = xlwt.XFStyle()
    data_alignment = xlwt.Alignment()
    data_alignment.wrap = 1
    data_alignment.vert = xlwt.Alignment.VERT_TOP
    data_style.alignment = data_alignment

    # Headers
    columns = [
        'Fecha de Creación',
        'Código',
        'Cliente',
        'Entrada',
        'Salida',
        'Posición',
        'SKU',
        'Condición',
        'Fecha de Vencimiento',
        'Productos',
        'Cantidad Total',
        'Cantidad Reservada',
        'Cantidad Disponible',
        'UOM',
        'Lote',
        'Estado',
        'Fecha de Despachado'
    ]

    row_num = 0
    for col_num, column_title in enumerate(columns):
        ws.write(row_num, col_num, column_title, header_style)
        ws.row(row_num).height_mismatch = True
        ws.row(row_num).height = 400

    # Pre-calcular reservas e inventario total por producto y cliente para optimizar consultas
    # Obtener todos los productos únicos y sus clientes del queryset
    product_client_pairs = {}
    for bin in bins_queryset:
        if not bin.client:
            continue
        for bep in bin.bin_products.all():
            if bep.entry_product and bep.entry_product.product:
                product = bep.entry_product.product
                client = bin.client
                key = (product.id, client.id)
                if key not in product_client_pairs:
                    product_client_pairs[key] = {
                        'product': product,
                        'client': client
                    }

    # Pre-calcular reservas directamente por BinProduct (más preciso)
    # Obtener todos los BinProduct del queryset
    bin_product_ids = list(models.BinProduct.objects.filter(
        bin__in=bins_queryset
    ).values_list('id', flat=True))

    # Calcular reservas por BinProduct específico
    bep_reservations = {}
    if bin_product_ids:
        # Obtener todas las reservas (ExitProduct no procesados) para estos BinProduct
        reserved_exit_products = ExitProduct.objects.filter(
            bin_product_id__in=bin_product_ids,
            exit_product_group__exit__status__in=[
                OrderStatus.PENDIENTE, OrderStatus.EN_PROGRESO],
            is_processed=False
        ).values('bin_product_id').annotate(
            total_reserved=Sum('quantity')
        )

        for item in reserved_exit_products:
            bep_reservations[item['bin_product_id']
                             ] = item['total_reserved'] or 0

    # Pre-calcular salidas relacionadas a cada bin para optimizar consultas
    # Reutilizar bin_product_ids ya calculado arriba

    # Inicializar diccionario para todos los bins
    bin_exit_codes = {}
    for bin in bins_queryset:
        bin_exit_codes[bin.id] = set()

    # Obtener todas las salidas relacionadas a estos BinProduct
    if bin_product_ids:
        exit_products = ExitProduct.objects.filter(
            bin_product_id__in=bin_product_ids
        ).select_related(
            'exit_product_group__exit',
            'bin_product__bin'
        ).distinct()

        # Agrupar salidas por bin
        for ep in exit_products:
            try:
                bin_id = ep.bin_product.bin.id
                exit_code = ep.exit_product_group.exit.code
                if bin_id in bin_exit_codes:
                    bin_exit_codes[bin_id].add(exit_code)
            except (AttributeError, Exception):
                continue

    # Convertir sets a strings ordenados
    for bin_id in bin_exit_codes:
        exit_codes = sorted(list(bin_exit_codes[bin_id]))
        bin_exit_codes[bin_id] = "\n".join(
            exit_codes) if exit_codes else "N/A"

    # Data rows
    for bin in bins_queryset:
        row_num += 1

        # Get separate lists for each field
        product_skus_list = []
        conditions_list = []
        expiration_dates_list = []
        products_list = []
        quantities_list = []
        available_quantities_list = []
        reserved_quantities_list = []
        uoms_list = []
        batches_list = []

        for bep in bin.bin_products.all():
            # Product SKU
            product_sku = bep.entry_product.product.client_sku if bep.entry_product else "N/A"
            product_skus_list.append(product_sku)

            # Condition
            condition = bep.entry_product.product.condition if bep.entry_product and bep.entry_product.product else "N/A"
            conditions_list.append(condition or "N/A")

            # Expiration date
            if bep.expiration_date:
                expiration_date = bep.expiration_date.strftime('%d/%m/%Y')
            else:
                expiration_date = "N/A"
            expiration_dates_list.append(expiration_date)

            # Product name
            product_name = bep.entry_product.product.name if bep.entry_product else "N/A"
            products_list.append(product_name)

            # Quantity (cantidad total en el bin)
            quantity = bep.quantity or 0
            quantities_list.append(str(quantity))

            # Calcular cantidad disponible y reservada
            available_quantity = quantity
            reserved_quantity = 0
            if bin.status == BinStatus.ALMACENADO:
                # Obtener reserva directa para este BinProduct específico
                reserved_quantity = bep_reservations.get(bep.id, 0)
                # La cantidad disponible es la cantidad total menos las reservas
                available_quantity = max(0, quantity - reserved_quantity)
            else:
                # Si el bin no está ALMACENADO, no hay disponibilidad
                available_quantity = 0

            available_quantities_list.append(str(available_quantity))
            reserved_quantities_list.append(str(reserved_quantity))

            # UOM
            uom = bep.entry_product.product.uom if bep.entry_product else "N/A"
            uoms_list.append(uom)

            # Batch
            batch = bep.batch or "Sin lote"
            batches_list.append(batch)

        # Convert lists to strings separated by line breaks
        product_skus_str = "\n".join(product_skus_list)
        conditions_str = "\n".join(conditions_list)
        expiration_dates_str = "\n".join(expiration_dates_list)
        products_str = "\n".join(products_list)
        quantities_str = "\n".join(quantities_list)
        available_quantities_str = "\n".join(available_quantities_list)
        reserved_quantities_str = "\n".join(reserved_quantities_list)
        uoms_str = "\n".join(uoms_list)
        batches_str = "\n".join(batches_list)

        # Get position information
        position_str = bin.position.code if bin.position else "Sin posición"

        # Get exit codes for this bin
        exit_codes_str = bin_exit_codes.get(bin.id, "N/A")

        # Write data row, convert dates to string to avoid timezone issues
        created_date_str = bin.created_at.strftime('%d/%m/%Y')

        # Format dispatched date
        dispatched_date_str = "N/A"
        if bin.dispatched_date:
            dispatched_date_str = bin.dispatched_date.strftime(
                '%d/%m/%Y %H:%M')

        # Write columns in the new order:
        # 0: Fecha de Creación, 1: Código, 2: Cliente, 3: Entrada, 4: Salida,
        # 5: Posición, 6: SKU, 7: Condición, 8: Fecha de Vencimiento, 9: Productos,
        # 10: Cantidad Total, 11: Cantidad Reservada, 12: Cantidad Disponible, 13: UOM, 14: Lote,
        # 15: Estado, 16: Fecha de Despachado
        ws.write(row_num, 0, created_date_str, data_style)
        ws.write(row_num, 1, bin.code, data_style)
        ws.write(
            row_num, 2, bin.client.name if bin.client else "N/A", data_style)
        ws.write(row_num, 3, bin.entry.code, data_style)
        ws.write(row_num, 4, exit_codes_str, data_style)
        ws.write(row_num, 5, position_str, data_style)
        ws.write(row_num, 6, product_skus_str, data_style)
        ws.write(row_num, 7, conditions_str, data_style)
        ws.write(row_num, 8, expiration_dates_str, data_style)
        ws.write(row_num, 9, products_str, data_style)
        ws.write(row_num, 10, quantities_str, data_style)
        ws.write(row_num, 11, reserved_quantities_str, data_style)
        ws.write(row_num, 12, available_quantities_str, data_style)
        ws.write(row_num, 13, uoms_str, data_style)
        ws.write(row_num, 14, batches_str, data_style)
        ws.write(row_num, 15, bin.get_status_display(), data_style)
        ws.write(row_num, 16, dispatched_date_str, data_style)

    # Adjust column widths
    for col_num in range(len(columns)):
        ws.col(col_num).width = 256 * 30

    # Save to buffer
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return output


def export_products_excel(products_queryset):
    """
    Exporta productos a Excel (respeta el queryset ya filtrado).
    """
    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Productos')

    header_style = xlwt.XFStyle()
    header_font = xlwt.Font()
    header_font.bold = True
    header_font.colour_index = xlwt.Style.colour_map['white']
    header_style.font = header_font

    header_pattern = xlwt.Pattern()
    header_pattern.pattern = xlwt.Pattern.SOLID_PATTERN
    header_pattern.pattern_fore_colour = xlwt.Style.colour_map['dark_blue']
    header_style.pattern = header_pattern

    header_alignment = xlwt.Alignment()
    header_alignment.horz = xlwt.Alignment.HORZ_CENTER
    header_alignment.vert = xlwt.Alignment.VERT_CENTER
    header_style.alignment = header_alignment

    data_style = xlwt.XFStyle()
    data_alignment = xlwt.Alignment()
    data_alignment.wrap = 1
    data_alignment.vert = xlwt.Alignment.VERT_TOP
    data_style.alignment = data_alignment

    columns = [
        'Código',
        'Nombre',
        'Cliente',
        'SKU',
        'Categoría',
        'Condición',
        'Rotación',
        'UOM',
        'Inventario',
        'Picking',
        'Activo',
        'Proveedor',
        'Comentario',
    ]

    for col_num, column_title in enumerate(columns):
        ws.write(0, col_num, column_title, header_style)
        ws.row(0).height_mismatch = True
        ws.row(0).height = 400

    qs = products_queryset.select_related('client', 'category')

    for row_num, product in enumerate(qs, start=1):
        inventory_total = (
            models.BinProduct.objects.filter(
                entry_product__product=product,
                bin__status=BinStatus.ALMACENADO,
            ).aggregate(total=Sum('quantity'))['total']
            or 0
        )

        ws.write(row_num, 0, product.code or '', data_style)
        ws.write(row_num, 1, product.name or '', data_style)
        ws.write(
            row_num,
            2,
            product.client.name if product.client_id else '',
            data_style,
        )
        ws.write(row_num, 3, product.client_sku or '', data_style)
        ws.write(
            row_num,
            4,
            product.category.name if product.category_id else '',
            data_style,
        )
        ws.write(
            row_num,
            5,
            product.get_condition_display() if product.condition else '',
            data_style,
        )
        ws.write(
            row_num,
            6,
            product.get_rotation_type_display() if product.rotation_type else '',
            data_style,
        )
        ws.write(
            row_num,
            7,
            product.get_uom_display() if product.uom else '',
            data_style,
        )
        ws.write(row_num, 8, inventory_total, data_style)
        ws.write(row_num, 9, 'Sí' if product.is_picking else 'No', data_style)
        ws.write(
            row_num,
            10,
            'Sí' if getattr(product, 'is_active', True) else 'No',
            data_style,
        )
        ws.write(row_num, 11, product.supplier or '', data_style)
        ws.write(row_num, 12, product.comment or '', data_style)

    for col_num in range(len(columns)):
        ws.col(col_num).width = 256 * 28

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output
