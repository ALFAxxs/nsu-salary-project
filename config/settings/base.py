"""
Base Django settings shared by all environments.

Environment-specific overrides live in dev.py / prod.py.
Secrets are read from the environment (.env) — never hard-coded.
"""
from pathlib import Path
import os

from dotenv import load_dotenv

# BASE_DIR points at the project root (where manage.py lives).
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load .env from project root as early as possible.
load_dotenv(BASE_DIR / ".env")


def env(key: str, default=None, required: bool = False):
    """Read an environment variable with an optional default."""
    value = os.environ.get(key, default)
    if required and value is None:
        raise RuntimeError(f"Required environment variable '{key}' is not set.")
    return value


def env_bool(key: str, default: bool = False) -> bool:
    return str(env(key, str(default))).strip().lower() in {"1", "true", "yes", "on"}


def env_list(key: str, default: str = "") -> list[str]:
    raw = env(key, default) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #
SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")

AUTH_USER_MODEL = "accounts.User"

# django.contrib.messages tags its ERROR level "error", but Bootstrap 5 (used
# throughout templates/) only defines alert-danger — without this mapping
# every messages.error(...) call renders as an unstyled, un-colored alert.
from django.contrib.messages import constants as _messages_constants  # noqa: E402
MESSAGE_TAGS = {
    _messages_constants.ERROR: "danger",
}

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "django_filters",
    # Local
    "apps.accounts",
    "apps.organizations",
    "apps.employees",
    "apps.salaries",
    "apps.imports",
    "apps.notifications",
    "apps.reports",
    "apps.audit",
    "apps.telegram_bot",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.audit.middleware.CurrentRequestMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.accounts.context_processors.sidebar_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "salary_db"),
        "USER": env("POSTGRES_USER", "salary_user"),
        "PASSWORD": env("POSTGRES_PASSWORD", ""),
        "HOST": env("POSTGRES_HOST", "127.0.0.1"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "60")),
    }
}

# --------------------------------------------------------------------------- #
# Password validation
# --------------------------------------------------------------------------- #
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --------------------------------------------------------------------------- #
# I18N / TZ
# --------------------------------------------------------------------------- #
LANGUAGE_CODE = "uz"
TIME_ZONE = env("TIME_ZONE", "Asia/Tashkent")
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------- #
# Static / Media
# --------------------------------------------------------------------------- #
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------- #
# Auth redirects
# --------------------------------------------------------------------------- #
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:index"
LOGOUT_REDIRECT_URL = "accounts:login"

# --------------------------------------------------------------------------- #
# File uploads
# --------------------------------------------------------------------------- #
# Max upload size for Excel files (bytes). Enforced in the import form/view too.
MAX_UPLOAD_SIZE = int(env("MAX_UPLOAD_SIZE", str(10 * 1024 * 1024)))  # 10 MB
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE
# Only .xlsx is accepted: the validator parses uploads with openpyxl, which
# cannot read the legacy binary .xls format, so advertising .xls support
# would just fail every such upload with a confusing "couldn't read file"
# error. If .xls support is ever needed, add xlrd and branch the parser.
ALLOWED_UPLOAD_EXTENSIONS = [".xlsx"]
ALLOWED_UPLOAD_MIME_TYPES = [
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",  # some browsers send this for .xlsx
]

# --------------------------------------------------------------------------- #
# REST Framework
# --------------------------------------------------------------------------- #
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": "1000/hour",
        "login": "10/min",
    },
}

# --------------------------------------------------------------------------- #
# Celery
# --------------------------------------------------------------------------- #
CELERY_BROKER_URL = env("REDIS_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = env("REDIS_URL", "redis://127.0.0.1:6379/0")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# --------------------------------------------------------------------------- #
# Cache (also backs login-attempt throttling — shared across gunicorn workers)
# --------------------------------------------------------------------------- #
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", "redis://127.0.0.1:6379/0"),
    }
}

# --------------------------------------------------------------------------- #
# Login throttling (apps.accounts.views.AppLoginView)
# --------------------------------------------------------------------------- #
# The DRF "login" scope in REST_FRAMEWORK above only applies to DRF views —
# the actual login form is a plain Django view, so it needs its own limiter.
LOGIN_RATE_LIMIT_ATTEMPTS = int(env("LOGIN_RATE_LIMIT_ATTEMPTS", "10"))
LOGIN_RATE_LIMIT_WINDOW = int(env("LOGIN_RATE_LIMIT_WINDOW", "300"))  # seconds

# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", "")
# Global Telegram sends/second budget for the notification sender.
TELEGRAM_SEND_RATE_LIMIT = int(env("TELEGRAM_SEND_RATE_LIMIT", "25"))
TELEGRAM_MAX_ATTEMPTS = int(env("TELEGRAM_MAX_ATTEMPTS", "3"))

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} [{levelname}] {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
        "application": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "application.log",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "verbose",
        },
        "telegram": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "telegram.log",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "verbose",
        },
        "celery": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "celery.log",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "verbose",
        },
        "security": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "security.log",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django": {"handlers": ["console", "application"], "level": "INFO"},
        "django.security": {"handlers": ["security"], "level": "INFO", "propagate": False},
        "apps": {"handlers": ["console", "application"], "level": "INFO"},
        "apps.telegram_bot": {"handlers": ["console", "telegram"], "level": "INFO", "propagate": False},
        "apps.notifications": {"handlers": ["console", "telegram"], "level": "INFO", "propagate": False},
        "celery": {"handlers": ["console", "celery"], "level": "INFO"},
        "security": {"handlers": ["security"], "level": "INFO", "propagate": False},
    },
}
