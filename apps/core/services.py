import random
import string
from io import BytesIO

import xlwt
from django.apps import apps


def generate_random_id(length=15):
    return ''.join(random.choices(string.ascii_letters, k=length))


def generate_slug(name, limit):
    slug = name.replace(' ', '')
    return slug[0:limit].upper()


def get_installed_apps():
    return [
        app_config.label for app_config in apps.get_app_configs()
        if app_config.name.startswith('apps.')
    ]


def export_clients_excel(clients_queryset):
    """Exporta clientes a Excel (respeta el queryset ya filtrado)."""
    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Clientes')

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
        'Nombre',
        'Compañía',
        'RUC',
        'DV',
        'Dirección',
        'Tarifa',
        'Pos. refrigeradas',
        'Pos. congeladas',
        'Slug',
        'Activo',
        'Creado',
    ]

    for col_num, title in enumerate(columns):
        ws.write(0, col_num, title, header_style)
        ws.row(0).height_mismatch = True
        ws.row(0).height = 400

    qs = clients_queryset.select_related('tariff')

    for row_num, client in enumerate(qs, start=1):
        created = ''
        if getattr(client, 'created_at', None):
            created = client.created_at.strftime('%Y-%m-%d %H:%M')

        ws.write(row_num, 0, client.name or '', data_style)
        ws.write(row_num, 1, client.company or '', data_style)
        ws.write(row_num, 2, client.ruc or '', data_style)
        ws.write(row_num, 3, client.dv or '', data_style)
        ws.write(row_num, 4, client.address or '', data_style)
        ws.write(
            row_num,
            5,
            client.tariff.name if client.tariff_id else '',
            data_style,
        )
        ws.write(row_num, 6, client.cold_positions or 0, data_style)
        ws.write(row_num, 7, client.frozen_positions or 0, data_style)
        ws.write(row_num, 8, client.slug or '', data_style)
        ws.write(
            row_num,
            9,
            'Sí' if getattr(client, 'is_active', True) else 'No',
            data_style,
        )
        ws.write(row_num, 10, created, data_style)

    for col_num in range(len(columns)):
        ws.col(col_num).width = 256 * 28

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output
