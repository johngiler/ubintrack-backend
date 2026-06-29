"""
Configuración Django — API multi-tenant WMS (Ubintrack).
"""
import os
from pathlib import Path

from corsheaders.defaults import default_headers
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-dev-only-ubintrack")
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() in ("1", "true", "yes")
DJANGO_ENV = os.environ.get("DJANGO_ENV", "development").strip().lower()

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]
if DEBUG:
    for _suffix in (".localhost", ".lvh.me"):
        if _suffix not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(_suffix)

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

if os.environ.get("USE_SQLITE", "").lower() in ("1", "true", "yes"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "ubintrack"),
            "USER": os.environ.get("POSTGRES_USER", "ubintrack"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
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

# Multi-tenant SaaS
DEFAULT_WORKSPACE_SLUG = os.environ.get("DEFAULT_WORKSPACE_SLUG", "rbcold").strip().lower() or "rbcold"
TENANT_BASE_DOMAIN = os.environ.get("TENANT_BASE_DOMAIN", "").strip().lower()
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "").strip().rstrip("/")
FRONTEND_DEV_PORT = os.environ.get("FRONTEND_DEV_PORT", "3000")

if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
    CORS_ALLOWED_ORIGINS = []
else:
    CORS_ALLOW_ALL_ORIGINS = False
    CORS_ALLOWED_ORIGINS = [
        o.strip()
        for o in os.environ.get(
            "CORS_ALLOWED_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if o.strip()
    ]
    _csrf_origins = os.environ.get("CSRF_TRUSTED_ORIGINS", "")
    if _csrf_origins.strip():
        CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_origins.split(",") if o.strip()]

CORS_ALLOW_HEADERS = (*default_headers, "x-workspace-slug", "x-tenant-slug")

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
    "DEFAULT_PAGINATION_CLASS": "apps.common.utils.pagination.StandardPagination",
    "PAGE_SIZE": 20,
}

try:
    from .local_settings import *  # noqa: F403
except ImportError:
    pass

if not DEBUG and TENANT_BASE_DOMAIN:
    import re as _re_cors

    _apex_esc = _re_cors.escape(TENANT_BASE_DOMAIN.strip().lower())
    _spa_tenant_pattern = rf"^https://[a-z0-9]([a-z0-9-]*[a-z0-9])?\.{_apex_esc}$"
    _cur = list(globals().get("CORS_ALLOWED_ORIGIN_REGEXES") or [])
    if _spa_tenant_pattern not in _cur:
        CORS_ALLOWED_ORIGIN_REGEXES = _cur + [_spa_tenant_pattern]
