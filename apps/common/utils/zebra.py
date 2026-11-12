import logging
import socket

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


def print_to_zebra(zpl_data, *, workspace=None):
    """
    Envía ZPL a la impresora de red del owner.

    Requiere workspace con impresora configurada (IP + puerto + ZPL).
    No usa variables de entorno globales.
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

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((printer_ip, printer_port))
        sock.send(zpl_data.encode("utf-8"))
        sock.close()
        return True
    except socket.timeout:
        logger.error(
            "Error: Timeout al conectar con la impresora %s:%s",
            printer_ip,
            printer_port,
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
