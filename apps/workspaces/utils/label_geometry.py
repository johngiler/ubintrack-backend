"""Geometría de etiqueta ZPL por workspace (baseline 4×2" @ 203 DPI)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

BASELINE_WIDTH_IN = Decimal("4.00")
BASELINE_HEIGHT_IN = Decimal("2.00")
BASELINE_DPI = 203
BASELINE_PW = 812  # 4" * 203
BASELINE_LL = 406  # 2" * 203


def load_workspace_for_label(workspace=None, *, workspace_id=None):
    """
    Carga el Workspace fresco desde BD (medida/DPI/idioma del owner).
    Evita usar instancias cacheadas o parciales sin campos de impresora.
    """
    from apps.workspaces.models import Workspace

    pk = workspace_id
    if pk is None and workspace is not None:
        pk = getattr(workspace, "pk", None) or getattr(workspace, "id", None)
    if not pk:
        return None
    return (
        Workspace.objects.filter(pk=pk)
        .only(
            "id",
            "label_width_in",
            "label_height_in",
            "printer_dpi",
            "printer_ip",
            "printer_port",
            "printer_language",
            "app_title",
            "name",
            "legal_name",
        )
        .first()
    )


def label_geometry(workspace=None) -> dict[str, Any]:
    """
    Dimensiones en dots y factores de escala respecto al layout 4×2 @ 203.

    Sin workspace (o valores inválidos) → baseline 4×2 @ 203.
    """
    width_in = BASELINE_WIDTH_IN
    height_in = BASELINE_HEIGHT_IN
    dpi = BASELINE_DPI

    if workspace is not None:
        try:
            width_in = Decimal(str(getattr(workspace, "label_width_in", width_in) or width_in))
        except Exception:
            width_in = BASELINE_WIDTH_IN
        try:
            height_in = Decimal(str(getattr(workspace, "label_height_in", height_in) or height_in))
        except Exception:
            height_in = BASELINE_HEIGHT_IN
        try:
            dpi = int(getattr(workspace, "printer_dpi", dpi) or dpi)
        except (TypeError, ValueError):
            dpi = BASELINE_DPI

    if width_in <= 0:
        width_in = BASELINE_WIDTH_IN
    if height_in <= 0:
        height_in = BASELINE_HEIGHT_IN
    if dpi < 72:
        dpi = BASELINE_DPI

    pw = max(1, int(round(float(width_in) * dpi)))
    ll = max(1, int(round(float(height_in) * dpi)))
    sx = pw / BASELINE_PW
    sy = ll / BASELINE_LL

    return {
        "width_in": width_in,
        "height_in": height_in,
        "dpi": dpi,
        "pw": pw,
        "ll": ll,
        "sx": sx,
        "sy": sy,
        "landscape": pw >= ll,
    }


def scale_dot(value: float | int, factor: float) -> int:
    return max(0, int(round(float(value) * factor)))


# Celda de packing ≈ 2" × 2" (paso fijo).
POSITION_CELL_INCHES = Decimal("2.00")


def position_label_grid(workspace=None) -> dict[str, int]:
    """
    Cuántas posiciones caben por etiqueta según medida del owner.
    cols = floor(ancho / 2"), rows = floor(alto / 2"), mín. 1.
    """
    geo = label_geometry(workspace)
    width_in = Decimal(str(geo["width_in"]))
    height_in = Decimal(str(geo["height_in"]))
    cols = max(1, int(width_in // POSITION_CELL_INCHES))
    rows = max(1, int(height_in // POSITION_CELL_INCHES))
    return {
        "cols": cols,
        "rows": rows,
        "max_per_label": cols * rows,
    }


def max_positions_per_label(workspace=None) -> int:
    return position_label_grid(workspace)["max_per_label"]
