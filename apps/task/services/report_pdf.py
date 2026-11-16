"""Informe PDF de control de inventario al completar una tarea."""

from __future__ import annotations

from io import BytesIO

from django.db import connection
from django.db.models import prefetch_related_objects
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.common.utils.media_layout import (
    build_wms_task_pdf_relative,
    ensure_media_parent_dir,
)
from apps.common.utils.pdf_branding import (
    append_workspace_logo,
    workspace_display_name,
    workspace_pdf_footer_paragraphs,
)
from apps.core.choices import TaskLineStatus
from apps.task.services.build_lines import bin_system_quantity

_LINE_STATUS_LABELS = {
    TaskLineStatus.PENDING: "Pendiente",
    TaskLineStatus.COUNTED: "Completo",
    TaskLineStatus.DISCREPANCY: "Incompleto",
    TaskLineStatus.MISSING_BIN: "No existe bin",
    TaskLineStatus.SKIPPED: "Omitido",
}


def _esc(value) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _line_system_qty(line) -> int | None:
    """Cantidad sistema: snapshot al contar, o fallback al bin actual."""
    stored = getattr(line, "system_quantity", None)
    if stored is not None:
        return int(stored)
    if line.bin_id:
        return int(bin_system_quantity(line.bin))
    return None


def _fmt_dt(value) -> str:
    if not value:
        return "—"
    try:
        local = timezone.localtime(value)
    except Exception:
        local = value
    try:
        return local.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return "—"


def _display_code(code: str) -> str:
    text = (code or "").strip()
    if not text:
        return "—"
    parts = text.split("-")
    if len(parts) >= 3 and parts[1].upper() == "TASK":
        return "-".join(parts[1:])
    return text


def _cell(text, style):
    return Paragraph(_esc(text), style)


def generate_task_pdf(task_instance) -> str:
    """
    Genera el informe PDF de la tarea y lo guarda en MEDIA.

    Returns:
        Ruta relativa del PDF.
    """
    connection.ensure_connection()
    task_instance.refresh_from_db()
    prefetch_related_objects(
        [task_instance],
        "assigned_to",
        "cavas",
        "products",
        "client",
        "workspace",
        "lines__position__cava",
        "lines__bin__client",
        "lines__bin__bin_products__entry_product__product",
    )

    relative_path = build_wms_task_pdf_relative(task_instance)
    filepath = ensure_media_parent_dir(relative_path)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        rightMargin=0.65 * inch,
    )

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="CenterTitle",
            fontSize=15,
            alignment=TA_CENTER,
            spaceAfter=8,
            leading=18,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionHead",
            fontSize=11,
            alignment=TA_LEFT,
            spaceBefore=10,
            spaceAfter=6,
            textColor=colors.HexColor("#0f172a"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="BodyLeft",
            fontSize=9,
            alignment=TA_LEFT,
            spaceBefore=2,
            spaceAfter=2,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="TableCell",
            fontSize=7.5,
            alignment=TA_LEFT,
            leading=9,
            wordWrap="CJK",
        )
    )
    styles.add(
        ParagraphStyle(
            name="TableCellCenter",
            fontSize=7.5,
            alignment=TA_CENTER,
            leading=9,
        )
    )
    styles.add(
        ParagraphStyle(
            name="TableHeader",
            fontSize=8,
            alignment=TA_CENTER,
            leading=10,
            textColor=colors.white,
            fontName="Helvetica-Bold",
        )
    )
    styles.add(
        ParagraphStyle(
            name="Muted",
            fontSize=8,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#64748b"),
            spaceBefore=2,
            spaceAfter=2,
        )
    )

    elements = []
    workspace = getattr(task_instance, "workspace", None)
    owner_label = workspace_display_name(workspace)

    # Logo arriba; si hay logo, no se muestra el nombre del owner.
    append_workspace_logo(elements, workspace, styles["CenterTitle"], size=1.35)
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(Paragraph("GERENCIA DE OPERACIONES", styles["CenterTitle"]))
    elements.append(
        Paragraph("INFORME DE CONTROL DE INVENTARIO", styles["CenterTitle"])
    )
    elements.append(
        Paragraph(
            f"Documento generado el {_fmt_dt(timezone.now())}",
            styles["Muted"],
        )
    )
    elements.append(Spacer(1, 0.15 * inch))

    # --- Datos de la tarea ---
    elements.append(Paragraph("<b>1. Datos de la tarea</b>", styles["SectionHead"]))

    client_name = (
        task_instance.client.name if task_instance.client_id else "Sin cliente"
    )
    cava_names = ", ".join(
        c.name or c.code for c in task_instance.cavas.all()
    ) or "Todas"
    halls = task_instance.halls or []
    halls_text = ", ".join(str(h) for h in halls) if halls else "Todos"
    product_names = ", ".join(
        p.name or p.client_sku for p in task_instance.products.all()
    ) or "Todos"
    assignees = ", ".join(
        u.get_full_name() or u.username for u in task_instance.assigned_to.all()
    ) or "—"

    meta_rows = [
        ["Código", _display_code(task_instance.code)],
        ["Título", task_instance.title or "Sin título"],
        ["Tipo", task_instance.get_task_type_display()],
        ["Estado", task_instance.get_status_display()],
        ["Prioridad", task_instance.get_priority_display()],
        ["Cliente", client_name],
        ["Cámaras", cava_names],
        ["Pasillos", halls_text],
        ["Productos", product_names],
        ["Inicio", _fmt_dt(task_instance.started_at)],
        ["Fin", _fmt_dt(task_instance.completed_at)],
        ["Asignados", assignees],
        [
            "Requiere aprobación",
            "Sí" if task_instance.require_approval else "No",
        ],
    ]
    meta_data = [
        [
            _cell(label, styles["TableCell"]),
            _cell(value, styles["TableCell"]),
        ]
        for label, value in meta_rows
    ]
    meta_table = Table(meta_data, colWidths=[1.7 * inch, 5.3 * inch])
    meta_table.hAlign = "LEFT"
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(meta_table)

    # --- Resumen del resultado ---
    lines = list(
        task_instance.lines.select_related(
            "position__cava", "bin__client"
        )
        .prefetch_related("bin__bin_products__entry_product__product")
        .order_by("sort_order", "id")
    )
    total = len(lines)
    by_status = {key: 0 for key in _LINE_STATUS_LABELS}
    qty_system = 0
    qty_counted = 0
    qty_diff_abs = 0
    qty_diff_net = 0
    for line in lines:
        by_status[line.line_status] = by_status.get(line.line_status, 0) + 1
        sys_q = _line_system_qty(line)
        cnt_q = line.counted_quantity
        if sys_q is not None:
            qty_system += int(sys_q)
        if cnt_q is not None:
            qty_counted += int(cnt_q)
        if (
            line.line_status == TaskLineStatus.DISCREPANCY
            and sys_q is not None
            and cnt_q is not None
        ):
            delta = int(cnt_q) - int(sys_q)
            qty_diff_abs += abs(delta)
            qty_diff_net += delta

    incomplete_n = by_status.get(TaskLineStatus.DISCREPANCY, 0)
    missing_n = by_status.get(TaskLineStatus.MISSING_BIN, 0)
    complete_n = by_status.get(TaskLineStatus.COUNTED, 0)

    elements.append(
        Paragraph("<b>2. Resumen del resultado</b>", styles["SectionHead"])
    )
    summary_rows = [
        ["Concepto", "Cantidad"],
        ["Líneas totales", str(total)],
        ["Completo", str(complete_n)],
        ["Incompleto", str(incomplete_n)],
        ["No existe bin", str(missing_n)],
        ["Omitido", str(by_status.get(TaskLineStatus.SKIPPED, 0))],
        ["Pendiente", str(by_status.get(TaskLineStatus.PENDING, 0))],
        ["Cantidad sistema (suma)", str(qty_system)],
        ["Cantidad contada (suma)", str(qty_counted)],
        [
            "Desvío neto (contada − sistema)",
            f"{qty_diff_net:+d}" if qty_diff_net else "0",
        ],
        ["Desvío absoluto (incompletos)", str(qty_diff_abs)],
    ]
    summary_data = [
        [
            _cell(
                label,
                styles["TableHeader"] if idx == 0 else styles["TableCell"],
            ),
            _cell(
                value,
                styles["TableHeader"] if idx == 0 else styles["TableCellCenter"],
            ),
        ]
        for idx, (label, value) in enumerate(summary_rows)
    ]
    summary_table = Table(summary_data, colWidths=[3.4 * inch, 1.4 * inch])
    summary_table.hAlign = "LEFT"
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("ALIGN", (1, 1), (1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(summary_table)

    if incomplete_n == 0 and missing_n == 0:
        result_note = (
            f"Recorrido cerrado sin diferencias: {complete_n} de {total} "
            "líneas completas."
        )
    else:
        bits = []
        if incomplete_n:
            bits.append(
                f"{incomplete_n} incompleta(s) "
                f"(neto {qty_diff_net:+d}, absoluto {qty_diff_abs})"
            )
        if missing_n:
            bits.append(f"{missing_n} bin(s) no encontrado(s)")
        result_note = (
            f"Se detectaron hallazgos: {'; '.join(bits)}. "
            f"Completas: {complete_n} de {total}."
        )
    elements.append(Spacer(1, 0.08 * inch))
    elements.append(
        Paragraph(f"<b>Conclusión:</b> {_esc(result_note)}", styles["BodyLeft"])
    )

    # --- Detalle ---
    elements.append(
        Paragraph("<b>3. Detalle del recorrido</b>", styles["SectionHead"])
    )
    if not lines:
        elements.append(
            Paragraph(
                "<i>No hay líneas registradas en esta tarea.</i>",
                styles["BodyLeft"],
            )
        )
    else:
        header = [
            Paragraph("Posición", styles["TableHeader"]),
            Paragraph("Bin", styles["TableHeader"]),
            Paragraph("Producto", styles["TableHeader"]),
            Paragraph("Sist.", styles["TableHeader"]),
            Paragraph("Cont.", styles["TableHeader"]),
            Paragraph("Resultado", styles["TableHeader"]),
            Paragraph("Notas", styles["TableHeader"]),
        ]
        data = [header]
        for line in lines:
            position_code = getattr(line.position, "code", None) or "—"
            bin_code = getattr(line.bin, "code", None) if line.bin_id else "—"
            product = "—"
            if line.bin_id:
                bps = list(line.bin.bin_products.all())
                if bps:
                    ep = getattr(bps[0], "entry_product", None)
                    prod = getattr(ep, "product", None) if ep else None
                    if prod is not None:
                        product = prod.name or prod.client_sku or "—"
            sys_q = _line_system_qty(line)
            sys_txt = "—" if sys_q is None else str(sys_q)
            cnt_txt = (
                "—" if line.counted_quantity is None else str(line.counted_quantity)
            )
            status_txt = _LINE_STATUS_LABELS.get(
                line.line_status, line.get_line_status_display()
            )
            data.append(
                [
                    _cell(position_code, styles["TableCell"]),
                    _cell(bin_code, styles["TableCell"]),
                    _cell(product, styles["TableCell"]),
                    _cell(sys_txt, styles["TableCellCenter"]),
                    _cell(cnt_txt, styles["TableCellCenter"]),
                    _cell(status_txt, styles["TableCell"]),
                    _cell(line.notes or "—", styles["TableCell"]),
                ]
            )

        detail = Table(
            data,
            colWidths=[
                1.15 * inch,
                1.05 * inch,
                1.55 * inch,
                0.55 * inch,
                0.55 * inch,
                0.9 * inch,
                1.25 * inch,
            ],
            repeatRows=1,
        )
        detail.hAlign = "LEFT"
        detail.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#94a3b8")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f8fafc")],
                    ),
                ]
            )
        )
        elements.append(detail)

    # --- Observaciones ---
    elements.append(Paragraph("<b>4. Observaciones</b>", styles["SectionHead"]))
    obs = (task_instance.observations or "").strip()
    elements.append(
        Paragraph(_esc(obs) if obs else "Sin observaciones.", styles["BodyLeft"])
    )

    # --- Firmas ---
    elements.append(Spacer(1, 0.55 * inch))
    signature_data = [
        ["____________________", "____________________"],
        ["Responsable de tarea", "Verificado por"],
        [owner_label, "Supervisor"],
    ]
    signature_table = Table(signature_data, colWidths=[2.6 * inch, 2.6 * inch])
    signature_table.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    elements.append(signature_table)

    footer = workspace_pdf_footer_paragraphs(workspace, styles["BodyLeft"])
    if footer:
        elements.append(Spacer(1, 0.45 * inch))
        elements.extend(footer)

    doc.build(elements)
    with open(filepath, "wb") as file:
        file.write(buffer.getvalue())
    return relative_path


def update_task_pdf(task_instance) -> str | None:
    """Genera el PDF y actualiza ``Task.pdf_document``."""
    import logging

    try:
        pdf_path = generate_task_pdf(task_instance)
    except Exception:
        logging.getLogger(__name__).exception(
            "No se pudo generar el PDF de la tarea %s",
            getattr(task_instance, "pk", None),
        )
        return None

    task_instance.pdf_document = pdf_path
    task_instance.save(update_fields=["pdf_document", "updated_at"])
    return pdf_path
