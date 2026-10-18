"""Phase 0 checks: Django runs without any relational DB or DB-backed contrib app."""
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password


def test_no_relational_database_configured() -> None:
    # DATABASES = {} in settings; once anything touches django.db.connections, Django
    # fills in a "default" entry that uses the dummy backend (which refuses every query).
    engine = settings.DATABASES.get("default", {}).get("ENGINE", "django.db.backends.dummy")
    assert engine == "django.db.backends.dummy"


def test_auth_app_not_installed() -> None:
    for app in ("django.contrib.auth", "django.contrib.admin", "django.contrib.contenttypes",
                "django.contrib.sessions"):
        assert app not in settings.INSTALLED_APPS


def test_hashers_work_without_auth_app() -> None:
    hashed = make_password("Spectra@2026")
    assert hashed.startswith("pbkdf2_sha256$")
    assert check_password("Spectra@2026", hashed)
    assert not check_password("wrong-password", hashed)
