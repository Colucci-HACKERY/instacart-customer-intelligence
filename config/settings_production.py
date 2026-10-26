
import os

from .settings import *

# Production must never fall back to the development secret.
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

# Production security.
DEBUG = False

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")
    if host.strip()
]

if not ALLOWED_HOSTS:
    raise RuntimeError(
        "Set DJANGO_ALLOWED_HOSTS to your deployment hostname."
    )

# WhiteNoise serves collected static files.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *[
        middleware
        for middleware in MIDDLEWARE
        if middleware != "django.middleware.security.SecurityMiddleware"
    ],
]

STATIC_ROOT = BASE_DIR / "staticfiles"

# The hosting platform should provide HTTPS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Set this to your actual HTTPS deployment URL when known.
csrf_origin = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").strip()
if csrf_origin:
    CSRF_TRUSTED_ORIGINS = [
        origin.strip()
        for origin in csrf_origin.split(",")
        if origin.strip()
    ]

# Optional paths for persistent production storage.
DATABASES["default"]["NAME"] = os.environ.get(
    "DJANGO_DATABASE_PATH",
    str(BASE_DIR / "db.sqlite3"),
)

RETENTION_ARTIFACT_PATH = os.environ.get(
    "DJANGO_RETENTION_ARTIFACT_PATH",
    str(BASE_DIR / "artifacts" / "retention-v1.sqlite3"),
)
