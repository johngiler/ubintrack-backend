"""
PRODUCTION — copiar en el servidor como config/settings/local_settings.py (gitignored).

  scp config/settings/local_settings.production.template.py \\
    ubintrack-api:/home/git/backend/config/settings/local_settings.py

Requiere backend/.env en el server (ver .env.production.template).
Generar SECRET_KEY: python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
"""
import os
import re

DEBUG = False
SECRET_KEY = "change-me-generate-with-django"  # reemplazar en el server; no versionar el valor real

DEFAULT_WORKSPACE_SLUG = "rbcold"
TENANT_BASE_DOMAIN = "ubintrack.com"
FRONTEND_URL_SCHEME = "https"

API_HOST = "api.ubintrack.com"
ALLOWED_HOSTS = [API_HOST, "127.0.0.1", "localhost"]

CSRF_TRUSTED_ORIGINS = [
    f"https://{API_HOST}",
    f"https://{DEFAULT_WORKSPACE_SLUG}.{TENANT_BASE_DOMAIN}",
]

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = []
_apex_esc = re.escape(TENANT_BASE_DOMAIN)
CORS_ALLOWED_ORIGIN_REGEXES = [
    rf"^https://[a-z0-9]([a-z0-9-]*[a-z0-9])?\.{_apex_esc}$",
]

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}
