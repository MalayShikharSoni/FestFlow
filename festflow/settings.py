"""
Django settings for FestFlow (Spectra 2026 ticketing & event ops).

Django is used ONLY for URLs, views, templates, forms, sessions and CSRF.
MongoDB is the only database and is reached through raw PyMongo in `repositories/`.
That is why there is no DATABASES entry and no DB-backed contrib app is installed.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Read .env into os.environ (real environment variables win over the file).
load_dotenv(BASE_DIR / ".env")

DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is not 1")
    # Dev-only fallback so a fresh clone runs; signed-cookie sessions depend on this key.
    SECRET_KEY = "dev-insecure-key-do-not-use-in-production"

ALLOWED_HOSTS = ["localhost", "127.0.0.1"] if DEBUG else []

# --- MongoDB (read by core/db.py) -------------------------------------------
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "spectra")
MONGO_TEST_DB_NAME = os.environ.get("MONGO_TEST_DB_NAME", "spectra_test")

# --- Apps --------------------------------------------------------------------
# No admin / auth / contenttypes / sessions apps: they all need a relational DB.
INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "django.contrib.messages",
    "core",
    "web",
]

# Not AuthenticationMiddleware — we do our own login against MongoDB `users`.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "festflow.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
                "core.auth.current_user",
            ],
        },
    },
]

WSGI_APPLICATION = "festflow.wsgi.application"

# --- No relational database --------------------------------------------------
DATABASES: dict = {}

# --- Sessions & messages without a database ----------------------------------
# Signed cookies: the session lives in the browser, signed with SECRET_KEY so it
# cannot be tampered with. We only store user_id and role, so it stays tiny.
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
SESSION_COOKIE_HTTPONLY = True
MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"

# --- Passwords ---------------------------------------------------------------
# make_password/check_password read this list; they do not need the auth app.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

# --- I18N / time -------------------------------------------------------------
# Datetimes are stored in MongoDB as UTC; templates render them in IST.
LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# --- Static files ------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
