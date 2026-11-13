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


def _workspace_for_bin(bin_instance):
    from apps.workspaces.utils.label_geometry import load_workspace_for_label

    ws_id = getattr(bin_instance, "workspace_id", None)
    if not ws_id:
        ws = getattr(bin_instance, "workspace", None) or workspace_from_client(
            getattr(bin_instance, "client", None)
        )
        ws_id = getattr(ws, "pk", None) if ws is not None else None
    return load_workspace_for_label(workspace_id=ws_id)


def _bin_label_layout(width: int, height: int) -> dict:
    """
    Layout 1×1: campos a la izquierda, QR grande arriba-derecha.
    QR ~ alto útil (mismo orden que posiciones); aire bajo el título.
    """
    width = max(200, int(width))
    height = max(100, int(height))
    pad = max(12, int(min(width, height) * 0.04))
    # 4×2 @ 203 → ~320–340 dots; fidelidad 1:1 vía bitmap/^GFA.
    qr_size = max(
        96,
        min(height - 2 * pad, int(height * 0.82), int(width * 0.42)),
    )
    title_size = max(28, min(64, int(height * 0.13)))
    field_size = max(14, min(28, int(height * 0.055)))
    title_gap = max(field_size, int(height * 0.045))
    text_col_w = max(80, width - qr_size - pad * 3)
    return {
        "width": width,
        "height": height,
        "pad": pad,
        "qr_size": qr_size,
        "qr_x": width - qr_size - pad,
        "qr_y": pad,
        "title_size": title_size,
        "field_size": field_size,
        "title_gap": title_gap,
        "text_col_w": text_col_w,
        "left": pad,
    }


def _bin_label_font(size: int):
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


def _text_pixel_width(draw, text: str, font) -> int:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]
    except AttributeError:
        return int(draw.textlength(text, font=font))


def _clip_to_width(text: str, font, draw, max_width: int) -> str:
    """Trunca con … para que el texto no invada el QR."""
    text = str(text or "-")
    if max_width <= 0:
        return text

    if _text_pixel_width(draw, text, font) <= max_width:
        return text
    ell = "..."
    if _text_pixel_width(draw, ell, font) >= max_width:
        return ell
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _text_pixel_width(draw, text[:mid] + ell, font) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ell if lo else ell


def _wrap_to_lines(text: str, font, draw, max_width: int, *, max_lines: int = 2) -> list[str]:
    """Parte el texto en hasta max_lines; la última lleva … si no cabe todo."""
    text = " ".join(str(text or "").split())
    if not text or max_width <= 0 or max_lines < 1:
        return []

    words = text.split(" ")
    lines: list[str] = []
    idx = 0
    while idx < len(words) and len(lines) < max_lines:
        is_last = len(lines) == max_lines - 1
        line = words[idx]
        idx += 1
        while idx < len(words):
            trial = f"{line} {words[idx]}"
            if _text_pixel_width(draw, trial, font) <= max_width:
                line = trial
                idx += 1
            else:
                break
        if is_last and idx < len(words):
            line = _clip_to_width(f"{line} {' '.join(words[idx:])}", font, draw, max_width)
            idx = len(words)
        lines.append(line)
    return lines


def _bin_label_value_present(value) -> bool:
    """True si el valor aporta información (no vacío ni guión solo)."""
    text = str(value or "").strip()
    return bool(text) and text != "-"


def _bin_label_observation_text(value: str) -> str:
    """Observación normalizada (espacios); el render parte hasta 2 líneas."""
    return " ".join(str(value or "").split())


def _bin_label_field_rows(bin_instance, workspace) -> list[tuple[str, str, str | None]]:
    """
    Filas de la etiqueta: solo campos con valor.
    Observación solo si el bin tiene label_include_observation.
    """
    rows: list[tuple[str, str, str | None]] = []

    company_name = (workspace_display_name(workspace) or "").upper().strip()
    if _bin_label_value_present(company_name):
        rows.append(("title", company_name, None))

    client = bin_instance.client.name if bin_instance.client else ""
    if _bin_label_value_present(client):
        rows.append(("field", "CLIENTE", client))

    position = ""
    if getattr(bin_instance, "position", None):
        position = bin_instance.position.code or ""
    if _bin_label_value_present(position):
        rows.append(("field", "POSICIÓN", position))

    bin_code = bin_instance.code or ""
    if _bin_label_value_present(bin_code):
        rows.append(("field", "BIN", bin_code))

    lpn = getattr(bin_instance, "lpn", None) or ""
    if _bin_label_value_present(lpn):
        rows.append(("field", "LPN", lpn))

    barcode = getattr(bin_instance, "barcode", None) or ""
    if _bin_label_value_present(barcode):
        rows.append(("field", "CÓDIGO DE BARRA", barcode))

    product_name = ""
    sku = ""
    lote = ""
    cantidad = ""
    exp = ""
    if isinstance(bin_instance, models.Bin):
        if hasattr(bin_instance, "bin_products") and bin_instance.bin_products.exists():
            bin_product = bin_instance.bin_products.first()
            product = bin_product.entry_product.product
            product_name = product.name or ""
            sku = product.client_sku or ""
            lote = bin_product.batch or bin_product.entry_product.batch or ""
            qty = bin_product.quantity
            uom = getattr(product, "uom", None) or ""
            if qty is not None and str(qty).strip() != "":
                cantidad = f"{qty} {uom}".strip()
            if bin_product.expiration_date:
                exp = bin_product.expiration_date.strftime("%Y-%m-%d")
            elif bin_product.entry_product.expiration_date:
                exp = bin_product.entry_product.expiration_date.strftime("%Y-%m-%d")

    for label, value in (
        ("PRODUCTO", product_name),
        ("SKU", sku),
        ("LOTE", lote),
        ("CANTIDAD", cantidad),
        ("EXPIRACIÓN", exp),
    ):
        if _bin_label_value_present(value):
            rows.append(("field", label, value))

    include_obs = bool(getattr(bin_instance, "label_include_observation", False))
    if include_obs:
        observation = _bin_label_observation_text(
            getattr(bin_instance, "observation", None) or ""
        )
        if _bin_label_value_present(observation):
            rows.append(("field", "OBSERVACIÓN", observation))

    return rows


def _render_bin_label_bitmap(bin_instance, width: int, height: int) -> Image.Image:
    """
    Arte 1×1 en dots/px (preview e impresión comparten este layout).
    Título + campos izq.; QR grande arriba-derecha.
    """
    workspace = _workspace_for_bin(bin_instance)
    layout = _bin_label_layout(width, height)
    pad = layout["pad"]
    qr_size = layout["qr_size"]
    title_size = layout["title_size"]
    field_size = layout["field_size"]
    title_gap = layout["title_gap"]
    text_col_w = layout["text_col_w"]
    left = layout["left"]
    line_gap = max(2, int(field_size * 0.2))

    # Observación un poco más chica que el resto de campos.
    obs_size = max(11, int(field_size * 0.82))
    obs_font = _bin_label_font(obs_size)
    obs_line_gap = max(2, int(obs_size * 0.2))
    # Separación superior respecto al QR / campos de arriba.
    obs_top_gap = max(obs_line_gap * 3, int(field_size * 0.7))

    rows_preview = _bin_label_field_rows(bin_instance, workspace)
    will_draw_obs = any(r[0] == "field" and r[1] == "OBSERVACIÓN" for r in rows_preview)
    # Franja inferior = exactamente 2 líneas de observación (+ aire), sin vacío extra.
    obs_band_top = None
    if will_draw_obs:
        obs_block_h = obs_size * 2 + obs_line_gap
        obs_band_h = obs_block_h + obs_top_gap
        obs_band_top = layout["height"] - pad - obs_band_h
        max_qr = max(96, obs_band_top - layout["qr_y"])
        qr_size = min(qr_size, max_qr)
        text_col_w = max(80, layout["width"] - qr_size - pad * 3)
        layout = {
            **layout,
            "qr_size": qr_size,
            "qr_x": layout["width"] - qr_size - pad,
            "text_col_w": text_col_w,
        }

    img = Image.new("RGB", (layout["width"], layout["height"]), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    title_font = _bin_label_font(title_size)
    text_font = _bin_label_font(field_size)

    def draw_bold(x, y, text, font):
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            draw.text((x + dx, y + dy), text, font=font, fill="black")

    def draw_labeled(x, y, label, value, font, max_w):
        prefix = f"{label}:"
        prefix_w = _text_pixel_width(draw, prefix, font) + 8
        value = _clip_to_width(value, font, draw, max(20, max_w - prefix_w))
        draw_bold(x, y, prefix, font)
        draw.text((x + prefix_w, y), value, font=font, fill="black")
        return field_size

    def draw_observation(x, y, value, font, max_w, *, size: int, gap: int):
        """
        Ancho completo (hueco bajo el QR):
        L1 = OBSERVACIÓN: + texto; L2 = resto a todo el ancho con ….
        """
        label = "OBSERVACIÓN:"
        label_w = _text_pixel_width(draw, label, font) + 8
        first_w = max(20, max_w - label_w)
        draw_bold(x, y, label, font)

        words = value.split(" ")
        line1_parts: list[str] = []
        idx = 0
        while idx < len(words):
            trial = " ".join(line1_parts + [words[idx]])
            if _text_pixel_width(draw, trial, font) <= first_w:
                line1_parts.append(words[idx])
                idx += 1
            else:
                break
        line1 = " ".join(line1_parts)
        if line1:
            draw.text((x + label_w, y), line1, font=font, fill="black")

        rest = " ".join(words[idx:])
        if rest:
            line2 = _wrap_to_lines(rest, font, draw, max_w, max_lines=1)
            if line2:
                draw.text((x, y + size + gap), line2[0], font=font, fill="black")
        elif not line1 and words:
            wrapped = _wrap_to_lines(value, font, draw, max_w, max_lines=2)
            for li, line in enumerate(wrapped):
                draw.text(
                    (x, y + (size + gap) * (li + 1)),
                    line,
                    font=font,
                    fill="black",
                )

    rows = rows_preview
    title_row = next((r for r in rows if r[0] == "title"), None)
    field_rows = [r for r in rows if r[0] == "field"]
    obs_row = next((r for r in field_rows if r[1] == "OBSERVACIÓN"), None)
    other_rows = [r for r in field_rows if r[1] != "OBSERVACIÓN"]

    # Título arriba (si hay); aire extra debajo.
    if title_row:
        title_text = _clip_to_width(title_row[1], title_font, draw, text_col_w)
        draw_bold(left, pad, title_text, title_font)
        fields_top = pad + title_size + title_gap
    else:
        fields_top = pad

    qr_bottom = layout["qr_y"] + qr_size
    full_text_w = layout["width"] - 2 * pad
    # Campos solo al costado del QR; la franja inferior es de observación.
    if obs_row and obs_band_top is not None:
        other_bottom = max(fields_top + field_size, min(qr_bottom, obs_band_top))
    else:
        other_bottom = layout["height"] - pad

    usable = max(1, other_bottom - fields_top)
    if other_rows:
        step = usable / len(other_rows)
        for index, row in enumerate(other_rows):
            y_pos = int(fields_top + index * step + max(0, (step - field_size) / 2))
            draw_labeled(left, y_pos, row[1], row[2], text_font, text_col_w)

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=1,
    )
    qr.add_data(bin_instance.code or "")
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_image = qr_image.resize((qr_size, qr_size), Image.Resampling.NEAREST)
    # Recortar margen blanco inferior del QR para no dejar “hueco falso”.
    qr_gray = qr_image.convert("L")
    qr_w, qr_h = qr_gray.size
    qr_px = qr_gray.load()
    ink_bottom = 0
    for yy in range(qr_h - 1, -1, -1):
        if any(qr_px[xx, yy] < 250 for xx in range(qr_w)):
            ink_bottom = yy
            break
    trim = max(0, qr_h - ink_bottom - 1 - max(2, qr_h // 40))
    if trim > 0:
        qr_image = qr_image.crop((0, 0, qr_w, qr_h - trim))
    img.paste(qr_image, (layout["qr_x"], layout["qr_y"]))
    qr_content_bottom = layout["qr_y"] + qr_image.size[1]

    if obs_row:
        # Anclar al borde inferior (sin vacío muerto abajo).
        # El aire bajo el QR / EXPIRACIÓN es la separación superior.
        block_h = obs_size * 2 + obs_line_gap
        obs_y = layout["height"] - pad - block_h
        min_obs_y = qr_content_bottom + obs_top_gap
        if obs_y < min_obs_y:
            obs_y = min_obs_y
            max_obs_y = layout["height"] - pad - block_h
            if obs_y > max_obs_y:
                obs_y = max_obs_y
        draw_observation(
            left,
            obs_y,
            obs_row[2],
            obs_font,
            full_text_w,
            size=obs_size,
            gap=obs_line_gap,
        )

    return img


def _bitmap_to_zpl_gfa(img: Image.Image, *, x: int, y: int) -> str:
    """PIL 1-bit → ^GFA (ASCII hex). Negro = bit 1. Misma vía que posiciones."""
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
                if pixels[px, row] == 0:
                    byte_val |= 0x80 >> bit
            raw.append(byte_val)
    total = len(raw)
    hex_data = raw.hex().upper()
    return f"^FO{x},{y}^GFA,{total},{total},{row_bytes},{hex_data}^FS\n"


def generate_bin_tag(bin_instance):
    """Etiqueta de bin (preview): mismo layout que ZPL; resolución pantallas."""
    from apps.workspaces.utils.label_geometry import label_geometry, scale_dot

    workspace = _workspace_for_bin(bin_instance)
    geo = label_geometry(workspace)
    # Preview baseline 4×2 @ 300 DPI (1200×600); escala con pulgadas del owner.
    tag_width = max(200, scale_dot(1200, geo["sx"]))
    tag_height = max(100, scale_dot(600, geo["sy"]))
    tag = _render_bin_label_bitmap(bin_instance, tag_width, tag_height)

    relative_path = build_wms_bin_tag_relative(bin_instance)
    tag_path = ensure_media_parent_dir(relative_path)
    tag.save(tag_path, format="WEBP", quality=85, method=6)
    return tag_path


def generate_bin_tag_zpl(bin_instance):
    """
    ZPL de bin 1×1: bitmap completo → ^GFA (fidelidad preview/print como posiciones).
    """
    from apps.workspaces.utils.label_geometry import label_geometry

    workspace = _workspace_for_bin(bin_instance)
    geo = label_geometry(workspace)
    pw, ll = int(geo["pw"]), int(geo["ll"])
    bitmap = _render_bin_label_bitmap(bin_instance, pw, ll)
    body = _bitmap_to_zpl_gfa(bitmap.convert("1"), x=0, y=0)
    return f"^XA\n^PW{pw}\n^LL{ll}\n^LH0,0\n{body}^XZ\n"


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
