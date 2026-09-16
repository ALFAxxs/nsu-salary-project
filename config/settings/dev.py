"""Development settings — DEBUG on, SQLite fallback allowed, relaxed security."""
from .base import *  # noqa
from .base import env, env_bool

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Allow SQLite in local dev if POSTGRES vars are not provided.
if not env("POSTGRES_PASSWORD"):
    DATABASES["default"] = {  # noqa: F405
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }

# Local dev shouldn't require a running Redis just to log in.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Run Celery tasks inline in dev unless a broker is explicitly requested.
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", True)
CELERY_TASK_EAGER_PROPAGATES = True

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
