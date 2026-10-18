"""Views. Rule: views call services (or core helpers), never PyMongo directly."""
from django.contrib.auth.hashers import check_password, make_password
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from core.db import health_check


def home(request: HttpRequest) -> HttpResponse:
    """Placeholder landing page until the event list arrives in Phase 5."""
    return render(request, "web/home.html")


def _hashers_work_without_auth_app() -> bool:
    """Prove make_password/check_password run with django.contrib.auth NOT installed.

    They only read settings.PASSWORD_HASHERS and never touch a User model or a
    database, so they are safe to use with our MongoDB `users` collection.
    """
    hashed = make_password("Spectra@2026")
    return check_password("Spectra@2026", hashed) and not check_password("wrong", hashed)


def health(request: HttpRequest) -> HttpResponse:
    """Ping MongoDB and list collections — the Phase 0 smoke test."""
    context = {
        "mongo": health_check(),
        "hashers_ok": _hashers_work_without_auth_app(),
    }
    status = 200 if context["mongo"]["ok"] else 503
    return render(request, "web/health.html", context, status=status)
