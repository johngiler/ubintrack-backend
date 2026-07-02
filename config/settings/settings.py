"""
Configuración común del proyecto + defaults de desarrollo local.

Convención:
- settings.py          → compartido + Django dev (SQLite, DEBUG=True, CORS abierto).
- local_settings.py    → vacío en dev (gitignored); copia de production en el servidor.
- local_settings.production.py → Django producción (gitignored; ver .example).
- .env                 → variables de SO/servicios (Postgres, Zebra, etc.); vacío en dev.
"""
import os
from pathlib import Path

from corsheaders.defaults import default_headers
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Solo variables de SO / servicios externos (no configuración Django).
load_dotenv(BASE_DIR / ".env")

# --- Desarrollo local (producción sobrescribe vía local_settings.py) ---
SECRET_KEY = "django-insecure-dev-only-ubintrack"
DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1", ".localhost", ".lvh.me"]

DEFAULT_WORKSPACE_SLUG = "rbcold"
TENANT_BASE_DOMAIN = "localhost"
FRONTEND_BASE_URL = ""
FRONTEND_DEV_PORT = "3000"
FRONTEND_URL_SCHEME = "http"

CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOWED_ORIGINS = []
CORS_ALLOW_HEADERS = (*default_headers, "x-workspace-slug")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "apps.common.apps.CommonConfig",
    "apps.workspaces.apps.WorkspacesConfig",
    "apps.core",
    "apps.inventory",
    "apps.storage",
    "apps.order",
    "apps.finance",
    "apps.task",
    "apps.dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.common.middleware.MediaCrossOriginResourcePolicyMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "apps.workspaces.middleware.TenantMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_currentuser.middleware.ThreadLocalUserMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es-pa"
TIME_ZONE = "America/Panama"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
_static_source = BASE_DIR / "static"
STATICFILES_DIRS = [_static_source] if _static_source.is_dir() else []

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "core.CustomUser"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.TokenAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
    ),
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.StandardPagination",
    "PAGE_SIZE": 20,
}

try:
    from .local_settings import *  # noqa: F403
except ImportError:
    pass
