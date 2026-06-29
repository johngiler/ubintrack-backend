"""Prueba de conexión SMTP transaccional (Mi negocio) sin persistir ni enviar correo."""

from __future__ import annotations

import logging
import smtplib
import socket
from typing import Any

from django.core.mail import get_connection

logger = logging.getLogger(__name__)


def _format_smtp_error(exc: BaseException) -> tuple[str, str]:
    """(mensaje legible, detalle técnico)."""
    tech = str(exc)
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            "El servidor rechazó el usuario o la contraseña. Revisa credenciales; "
            "algunos proveedores exigen una contraseña de aplicación.",
            tech,
        )
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return (
            "El servidor cerró la conexión. Suele indicar puerto equivocado o mezcla incorrecta "
            "entre STARTTLS (puerto 587) e SSL implícito (puerto 465).",
            tech,
        )
    if isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in tech.lower():
        return (
            "Tiempo de espera agotado al conectar con el SMTP. Revisa host, puerto y que el servidor "
            "permita conexiones desde este entorno.",
            tech,
        )
    if isinstance(exc, OSError):
        return ("No se pudo conectar al servidor (red, DNS o puerto incorrecto).", tech)
    return ("No se pudo completar la conexión de prueba.", tech)


def run_transactional_smtp_connection_test(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    use_tls: bool,
    use_ssl: bool,
    timeout: int = 25,
) -> dict[str, Any]:
    """
    Abre conexión al SMTP, negocia TLS/SSL si aplica e inicia sesión.
    No envía mensaje ni usa MAIL FROM / RCPT TO.

    Retorna dict con keys: ok (bool), detail (str), technical (str|None).
    """
    if use_tls and use_ssl:
        return {
            "ok": False,
            "detail": "No combines «Usar TLS» (STARTTLS, típico en 587) con «SSL implícito» (465). Elige solo uno.",
            "technical": None,
        }
    conn = get_connection(
        backend="django.core.mail.backends.smtp.EmailBackend",
        host=host.strip(),
        port=int(port),
        username=username.strip(),
        password=password.strip(),
        use_tls=use_tls,
        use_ssl=use_ssl,
        timeout=timeout,
    )
    try:
        conn.open()
    except Exception as exc:
        logger.info("Prueba de conexión SMTP fallida (%s): %s", host, exc)
        detail, technical = _format_smtp_error(exc)
        return {"ok": False, "detail": detail, "technical": technical}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return {
        "ok": True,
        "detail": "Conexión correcta: el servidor respondió y aceptó las credenciales.",
        "technical": None,
    }
