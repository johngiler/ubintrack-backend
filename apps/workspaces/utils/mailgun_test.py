from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


def _mailgun_base_url(region: str) -> str:
    r = (region or "").strip().lower()
    if r == "eu":
        return "https://api.eu.mailgun.net"
    return "https://api.mailgun.net"


def run_transactional_mailgun_auth_test(
    *,
    api_key: str,
    domain: str,
    region: str = "us",
) -> dict:
    """
    Prueba de credenciales Mailgun sin enviar correo.

    Verifica que la API key autentica y que el dominio existe/está accesible.
    """
    key = (api_key or "").strip()
    dom = (domain or "").strip()
    if not key:
        return {"ok": False, "detail": "Indica la API key de Mailgun.", "technical": ""}
    if not dom:
        return {"ok": False, "detail": "Indica el dominio de Mailgun.", "technical": ""}

    base = _mailgun_base_url(region)
    url = f"{base}/v3/domains/{dom}"
    try:
        res = requests.get(url, auth=("api", key), timeout=20)
    except Exception as exc:
        logger.info("Prueba Mailgun fallida (%s): %s", dom, exc)
        return {
            "ok": False,
            "detail": "No se pudo conectar con Mailgun. Revisa la red y la región.",
            "technical": repr(exc),
        }
    if res.status_code == 200:
        return {"ok": True, "detail": "Credenciales válidas. Mailgun respondió correctamente.", "technical": ""}
    if res.status_code in (401, 403):
        return {"ok": False, "detail": "API key inválida o sin permisos.", "technical": res.text[:800]}
    if res.status_code == 404:
        return {"ok": False, "detail": "Dominio no encontrado en Mailgun.", "technical": res.text[:800]}
    return {
        "ok": False,
        "detail": f"Respuesta inesperada de Mailgun (HTTP {res.status_code}).",
        "technical": res.text[:800],
    }

