"""
Django settings for Pulse.

Built for a demo: no login on the API. Locally it uses SQLite with DEBUG on.
Set DATABASE_URL (PostgreSQL) and DJANGO_DEBUG=False to run it hosted.
See the README sections "Deploy it" and "Before this goes anywhere near real patients".
"""
import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

# Non-empty values in backend/.env win over anything already set in your shell.
for _key, _value in dotenv_values(BASE_DIR / ".env").items():
    if _value not in (None, ""):
        os.environ[_key] = _value


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


_DEV_SECRET_KEY = "dev-only-insecure-key-replace-me-0f8a7c2e9b1d4e6f"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", _DEV_SECRET_KEY)
DEBUG = env_bool("DJANGO_DEBUG", True)
if not DEBUG and SECRET_KEY in (_DEV_SECRET_KEY, "change-me-to-any-long-random-string"):
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY to a long random string when DJANGO_DEBUG is off.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
# Render sets this to the service's public hostname.
_RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
if _RENDER_HOST:
    ALLOWED_HOSTS.append(_RENDER_HOST)
    CSRF_TRUSTED_ORIGINS = [f"https://{_RENDER_HOST}"]
if DEBUG:
    # Lets you open the demo from a phone on the same Wi-Fi (python manage.py runserver 0.0.0.0:8000)
    ALLOWED_HOSTS.append("*")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "triage",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if not DEBUG:
    # Serves the admin's CSS and JS when gunicorn runs the app (runserver does this itself).
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "pulse.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

WSGI_APPLICATION = "pulse.wsgi.application"

# SQLite on a laptop. Set DATABASE_URL (for example a Neon connection string) to use PostgreSQL.
if os.getenv("DATABASE_URL", "").strip():
    DATABASES = {"default": dj_database_url.config(conn_max_age=600, conn_health_checks=True)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "OPTIONS": {"timeout": 20},
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "en-ca"
TIME_ZONE = "America/Toronto"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
# The built React app (frontend/dist) is served by Django in demo mode.
STATICFILES_DIRS = [FRONTEND_DIST] if FRONTEND_DIST.exists() else []

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    # Demo mode: the API is open on localhost. Django admin (/admin) still needs a login.
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
}

# Front end hosted on another origin (for example Vercel). Comma-separated, no trailing slash.
CORS_ALLOWED_ORIGINS = env_list("PULSE_CORS_ORIGINS")

if not DEBUG:
    # Behind a TLS-terminating proxy (Render).
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Claude. App-specific names (PULSE_...) so other tools' environment variables can't collide.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("PULSE_CLAUDE_MODEL", "claude-sonnet-5-5").strip() or "claude-sonnet-5-5"
CLAUDE_EFFORT = os.getenv("PULSE_CLAUDE_EFFORT", "low").strip().lower() or "low"
# Always talk to the official API unless you deliberately point it elsewhere.
CLAUDE_BASE_URL = os.getenv("PULSE_ANTHROPIC_BASE_URL", "https://api.anthropic.com").strip()
# Cap on Claude requests per hour across the whole app. 0 means no cap (fine on a laptop).
# Set a cap on a public deployment so strangers can't run up your API bill.
try:
    CLAUDE_HOURLY_LIMIT = max(0, int(os.getenv("PULSE_CLAUDE_HOURLY_LIMIT", "0") or 0))
except ValueError:
    CLAUDE_HOURLY_LIMIT = 0

# Hosted demo: when the newest alert is older than this many hours, load a fresh synthetic shift
# on the next visit so the queue is never empty. 0 turns it off (fine on a laptop).
try:
    AUTO_RESEED_HOURS = max(0.0, float(os.getenv("PULSE_AUTO_RESEED_HOURS", "0") or 0))
except ValueError:
    AUTO_RESEED_HOURS = 0.0

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"triage": {"handlers": ["console"], "level": "INFO"}},
}
