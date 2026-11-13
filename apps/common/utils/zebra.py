import logging
import socket
import time

logger = logging.getLogger(__name__)

USER_PRINTER_NOT_CONFIGURED = "Configure la impresora."
USER_PRINT_FAILED = "No se pudo imprimir la etiqueta. Verifique la impresora."


def printer_is_configured(workspace) -> bool:
    """
    Impresión solo con config completa del owner: IP, puerto válido y lenguaje ZPL.
    Sin fallback de entorno (evita cruzar impresoras entre owners).
    """
    if workspace is None:
        return False
    host = (getattr(workspace, "printer_ip", None) or "").strip()
    if not host:
        return False
    try:
        port = int(getattr(workspace, "printer_port", None) or 0)
    except (TypeError, ValueError):
        return False
    if port < 1 or port > 65535:
        return False
    language = (getattr(workspace, "printer_language", None) or "").strip().lower()
    if language != "zpl":
        return False
    return True


def resolve_printer_endpoint(workspace) -> tuple[str, int] | None:
    """Host/puerto del workspace, o None si la config no está completa."""
    if not printer_is_configured(workspace):
        return None
    host = (workspace.printer_ip or "").strip()
    port = int(workspace.printer_port)
    return host, port


def _timeout_for_payload(nbytes: int) -> float:
    # Gráficos GFA son pesados; 5s no alcanza con varias hojas.
    return min(90.0, max(20.0, 15.0 + nbytes / 8000.0))


def print_to_zebra(zpl_data, *, workspace=None):
    """
    Envía un job ZPL (una etiqueta física) a la impresora del owner.
    """
    if not zpl_data:
        return False

    endpoint = resolve_printer_endpoint(workspace)
    if endpoint is None:
        logger.error(
            "Impresión bloqueada: impresora del owner no configurada (workspace_id=%s)",
            getattr(workspace, "pk", None),
        )
        return False

    printer_ip, printer_port = endpoint
    payload = zpl_data.encode("utf-8") if isinstance(zpl_data, str) else zpl_data
    timeout = _timeout_for_payload(len(payload))

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((printer_ip, printer_port))
        sock.sendall(payload)
        sock.close()
        return True
    except socket.timeout:
        logger.error(
            "Error: Timeout al conectar/enviar a la impresora %s:%s (%s bytes)",
            printer_ip,
            printer_port,
            len(payload),
        )
        return False
    except ConnectionRefusedError:
        logger.error(
            "Error: Conexión rechazada por la impresora %s:%s",
            printer_ip,
            printer_port,
        )
        return False
    except Exception as e:
        logger.error("Error al imprimir: %s", e)
        return False


def print_zpl_sheets(sheets, *, workspace=None, pause_s: float = 0.35) -> bool:
    """
    Envía cada etiqueta física en su propia conexión.
    Evita saturar el buffer al unir varios ^GFA grandes.
    """
    jobs = [s for s in (sheets or []) if s]
    if not jobs:
        return False
    if not printer_is_configured(workspace):
        return False

    for index, sheet in enumerate(jobs):
        if not print_to_zebra(sheet, workspace=workspace):
            logger.error(
                "Falló la hoja %s/%s al imprimir (workspace_id=%s)",
                index + 1,
                len(jobs),
                getattr(workspace, "pk", None),
            )
            return False
        if index + 1 < len(jobs) and pause_s > 0:
            time.sleep(pause_s)
    return True
