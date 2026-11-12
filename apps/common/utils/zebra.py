import os
import socket
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

logger = logging.getLogger(__name__)
# Fallback SO / .env cuando el owner no tiene IP configurada.
_DEFAULT_PRINTER_IP = os.getenv("ZEBRA_PRINTER_IP", "127.0.0.1")
_DEFAULT_PRINTER_PORT = int(os.getenv("ZEBRA_PRINTER_PORT", "9100"))


def resolve_printer_endpoint(workspace=None) -> tuple[str, int]:
    """Host/puerto del workspace; si IP vacía, fallback de entorno."""
    host = ""
    port = _DEFAULT_PRINTER_PORT
    if workspace is not None:
        host = (getattr(workspace, "printer_ip", None) or "").strip()
        try:
            raw_port = getattr(workspace, "printer_port", None)
            if raw_port:
                port = int(raw_port)
        except (TypeError, ValueError):
            port = _DEFAULT_PRINTER_PORT
    if not host:
        host = _DEFAULT_PRINTER_IP
    if port < 1 or port > 65535:
        port = _DEFAULT_PRINTER_PORT
    return host, port


def printer_language_supported(workspace=None) -> bool:
    if workspace is None:
        return True
    language = (getattr(workspace, "printer_language", None) or "zpl").strip().lower()
    return language == "zpl"


def print_to_zebra(zpl_data, *, workspace=None):
    """
    Envía datos ZPL a la impresora de red del owner.

    Args:
        zpl_data (str): Código ZPL a imprimir
        workspace: Workspace del owner (IP/puerto/lenguaje). Opcional → .env

    Returns:
        bool: True si la impresión fue exitosa, False si hubo error
    """
    if not zpl_data:
        return False

    if not printer_language_supported(workspace):
        language = getattr(workspace, "printer_language", None)
        logger.error("Lenguaje de impresora no soportado: %s", language)
        return False

    printer_ip, printer_port = resolve_printer_endpoint(workspace)

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
