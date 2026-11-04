"""
LOCAL — copiar a config/settings/local_settings.py (gitignored).

  cp config/settings/local_settings.local.template.py config/settings/local_settings.py

Requiere backend/.env (ver .env.local.template) y Postgres Docker:
  cd backend && docker-compose up -d
"""
import os

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
