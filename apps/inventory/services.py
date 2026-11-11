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
    workspace_display_name,
    workspace_from_client,
)

logger = logging.getLogger(__name__)


def generate_bin_tag(bin_instance):
    """Etiqueta de bin 4×2" (preview @ 300 DPI): campos + QR repartidos en todo el alto."""

    # 4×2" @ 300 DPI (misma proporción que ZPL 4×2 @ 203)
    tag_width = 1200
    tag_height = 600
    padding = 18
    qr_size = 340

    tag = Image.new("RGB", (tag_width, tag_height), (255, 255, 255))
    draw = ImageDraw.Draw(tag)

    try:
        font_path = os.path.join(settings.BASE_DIR, "arial.ttf")
        title_font = ImageFont.truetype(font_path, 32)
        text_font = ImageFont.truetype(font_path, 24)
    except OSError:
        try:
            title_font = ImageFont.truetype("Arial", 32)
            text_font = ImageFont.truetype("Arial", 24)
        except OSError:
            logger.error("No se pudo cargar ninguna fuente. Usando la fuente predeterminada.")
            title_font = ImageFont.load_default()
            text_font = ImageFont.load_default()

    def clip(text, max_chars):
        text = str(text or "-")
        return text if len(text) <= max_chars else text[: max_chars - 3] + "..."

    def draw_bold(x, y, text, font):
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            draw.text((x + dx, y + dy), text, font=font, fill="black")

    def text_width(text, font):
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            return bbox[2] - bbox[0]
        except AttributeError:
            return int(draw.textlength(text, font=font))

    def draw_labeled(x, y, label, value, font):
        prefix = f"{label}:"
        draw_bold(x, y, prefix, font)
        draw.text((x + text_width(prefix, font) + 8, y), value, font=font, fill="black")

    client = clip(bin_instance.client.name if bin_instance.client else "-", 36)
    position = "-"
    if hasattr(bin_instance, "position") and bin_instance.position:
        position = clip(bin_instance.position.code, 36)
    bin_code = clip(bin_instance.code, 36)
    workspace = workspace_from_client(bin_instance.client)
    company_name = clip(workspace_display_name(workspace), 38).upper()

    product_name = "-"
    sku = "-"
    lote = "-"
    cantidad = "-"
    exp = "-"
    if isinstance(bin_instance, models.Bin):
        if hasattr(bin_instance, "bin_products") and bin_instance.bin_products.exists():
            bin_product = bin_instance.bin_products.first()
            product = bin_product.entry_product.product
            product_name = clip(product.name, 36)
            sku = clip(product.client_sku or "-", 28)
            lote = clip(bin_product.batch or bin_product.entry_product.batch or "-", 28)
            cantidad = clip(f"{bin_product.quantity} {product.uom}", 28)
            if bin_product.expiration_date:
                exp = bin_product.expiration_date.strftime("%Y-%m-%d")
            elif bin_product.entry_product.expiration_date:
                exp = bin_product.entry_product.expiration_date.strftime("%Y-%m-%d")

    rows = [
        ("title", company_name, None),
        ("field", "CLIENTE", client),
        ("field", "POSICIÓN", position),
        ("field", "BIN", bin_code),
        ("field", "PRODUCTO", product_name),
        ("field", "SKU", sku),
        ("field", "LOTE", lote),
        ("field", "CANTIDAD", cantidad),
        ("field", "EXPIRACIÓN", exp),
    ]
    # Reparte título + campos en todo el alto útil.
    usable_h = tag_height - padding * 2
    step = usable_h / len(rows)
    for index, row in enumerate(rows):
        y_pos = int(padding + index * step + max(0, (step - 30) / 2))
        if row[0] == "title":
            draw_bold(padding, y_pos, row[1], title_font)
        else:
            draw_labeled(padding, y_pos, row[1], row[2], text_font)

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=1,
    )
    qr.add_data(bin_instance.code or "")
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_image = qr_image.resize((qr_size, qr_size))
    qr_x = tag_width - qr_size - padding
    qr_y = (tag_height - qr_size) // 2
    tag.paste(qr_image, (qr_x, qr_y))

    relative_path = build_wms_bin_tag_relative(bin_instance)
    tag_path = ensure_media_parent_dir(relative_path)
    tag.save(tag_path, format="WEBP", quality=85, method=6)

    return tag_path


def generate_bin_tag_zpl(bin_instance):
    """ZPL de bin 4×2" @ 203 DPI: campos en todo el alto + QR centrado."""

    def truncate_text_zpl(text, max_length=34):
        text = str(text or "")
        if len(text) > max_length:
            return text[: max_length - 3] + "..."
        return text

    def zpl_safe(text):
        # Sin tildes: emulación ZPL de escritorio suele ir en ASCII.
        text = str(text or "")
        for src, dst in (
            ("Á", "A"), ("É", "E"), ("Í", "I"), ("Ó", "O"), ("Ú", "U"),
            ("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"),
            ("Ñ", "N"), ("ñ", "n"),
        ):
            text = text.replace(src, dst)
        return text.replace("^", " ").replace("~", " ")

    def zpl_bold_label(x, y, label, value, size=22):
        prefix = f"{label}:"
        value_x = x + int(len(prefix) * size * 0.62) + 6
        return (
            f"^FO{x},{y}^A0N,{size},{size}^FD{prefix}^FS\n"
            f"^FO{x + 1},{y}^A0N,{size},{size}^FD{prefix}^FS\n"
            f"^FO{value_x},{y}^A0N,{size},{size}^FD{value}^FS\n"
        )

    client = bin_instance.client.name if bin_instance.client else "-"
    position = bin_instance.position.code if bin_instance.position else "-"
    bin_code = bin_instance.code or "-"
    workspace = workspace_from_client(bin_instance.client)
    company_name = truncate_text_zpl(
        zpl_safe(workspace_display_name(workspace)).upper(),
        34,
    )
    client = truncate_text_zpl(zpl_safe(client), 34)
    position = truncate_text_zpl(zpl_safe(position), 34)
    bin_code = zpl_safe(bin_code)

    product_name = "-"
    sku = "-"
    lote = "-"
    cantidad = "-"
    exp = "-"
    if isinstance(bin_instance, models.Bin):
        if hasattr(bin_instance, "bin_products") and bin_instance.bin_products.exists():
            bin_product = bin_instance.bin_products.first()
            product = bin_product.entry_product.product
            product_name = truncate_text_zpl(zpl_safe(product.name), 34)
            sku = truncate_text_zpl(zpl_safe(product.client_sku or "-"), 28)
            lote = truncate_text_zpl(
                zpl_safe(bin_product.batch or bin_product.entry_product.batch or "-"),
                28,
            )
            cantidad = truncate_text_zpl(
                zpl_safe(f"{bin_product.quantity} {product.uom}"),
                28,
            )
            if bin_product.expiration_date:
                exp = bin_product.expiration_date.strftime("%Y-%m-%d")
            elif bin_product.entry_product.expiration_date:
                exp = bin_product.entry_product.expiration_date.strftime("%Y-%m-%d")

    # 9 filas repartidas en 406 dots (márgenes ~14).
    rows = [
        ("title", company_name, None),
        ("field", "CLIENTE", client),
        ("field", "POSICION", position),
        ("field", "BIN", bin_code),
        ("field", "PRODUCTO", product_name),
        ("field", "SKU", sku),
        ("field", "LOTE", lote),
        ("field", "CANTIDAD", cantidad),
        ("field", "EXPIRACION", exp),
    ]
    top = 14
    usable = 406 - top * 2
    step = usable / len(rows)
    lines = []
    for index, row in enumerate(rows):
        y = int(top + index * step + max(0, (step - 24) / 2))
        if row[0] == "title":
            lines.append(
                f"^FO14,{y}^A0N,28,28^FD{row[1]}^FS\n"
                f"^FO15,{y}^A0N,28,28^FD{row[1]}^FS\n"
            )
        else:
            lines.append(zpl_bold_label(14, y, row[1], row[2], size=22))

    # QR mag 6 ≈ alto útil; centrado en vertical.
    lines.append("^FO520,50^BQN,2,6^FDMA," + f"{bin_code}^FS\n")
    return "^XA\n^PW812\n^LL406\n^LH0,0\n" + "".join(lines) + "^XZ\n"


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
        'LPN',
        'Código de barras',
        'Cliente',
        'Entrada',
        'Salida',
        'Posición',
        'SKU',
        'Condición',
        'Fecha de Producción',
        'Fecha de Vencimiento',
        'Productos',
        'Cantidad Total',
        'Cantidad Reservada',
        'Cantidad Disponible',
        'UOM',
        'Lote',
        'Estado',
        'Fecha de Despachado',
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
        production_dates_list = []
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

            # Production date (EntryProduct)
            prod_date = (
                bep.entry_product.production_date
                if bep.entry_product and bep.entry_product.production_date
                else None
            )
            production_dates_list.append(
                prod_date.strftime('%d/%m/%Y') if prod_date else "N/A"
            )

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
        production_dates_str = "\n".join(production_dates_list)
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

        ws.write(row_num, 0, created_date_str, data_style)
        ws.write(row_num, 1, bin.code, data_style)
        ws.write(row_num, 2, bin.lpn or "", data_style)
        ws.write(row_num, 3, bin.barcode or "", data_style)
        ws.write(
            row_num, 4, bin.client.name if bin.client else "N/A", data_style)
        ws.write(
            row_num,
            5,
            bin.entry.code if bin.entry_id else "N/A",
            data_style,
        )
        ws.write(row_num, 6, exit_codes_str, data_style)
        ws.write(row_num, 7, position_str, data_style)
        ws.write(row_num, 8, product_skus_str, data_style)
        ws.write(row_num, 9, conditions_str, data_style)
        ws.write(row_num, 10, production_dates_str, data_style)
        ws.write(row_num, 11, expiration_dates_str, data_style)
        ws.write(row_num, 12, products_str, data_style)
        ws.write(row_num, 13, quantities_str, data_style)
        ws.write(row_num, 14, reserved_quantities_str, data_style)
        ws.write(row_num, 15, available_quantities_str, data_style)
        ws.write(row_num, 16, uoms_str, data_style)
        ws.write(row_num, 17, batches_str, data_style)
        ws.write(row_num, 18, bin.get_status_display(), data_style)
        ws.write(row_num, 19, dispatched_date_str, data_style)

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
