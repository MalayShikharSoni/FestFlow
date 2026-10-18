"""
The one place where the MongoClient is created.

Why a single shared client?
    A MongoClient is not "a connection" — it is a connection *pool* plus
    background monitoring threads that track the server. It is thread-safe and
    meant to be created once per process and reused. Django serves requests on
    several threads, and every thread borrows a socket from this pool for the
    duration of one operation, then returns it. Creating a new client per request
    would open fresh sockets and monitoring threads every time (slow, and leaks
    resources). maxPoolSize (default 100) caps concurrent sockets.

Why tz_aware=True with UTC?
    BSON dates are always stored as UTC milliseconds. With tz_aware=True PyMongo
    returns timezone-aware datetimes (tzinfo=UTC) instead of naive ones, so
    comparing them with Django's timezone.now() never mixes naive and aware
    values. Templates then convert to Asia/Kolkata for display.
"""
import threading
from datetime import timezone
from typing import Any

from django.conf import settings
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import PyMongoError

_client: MongoClient | None = None
_client_lock = threading.Lock()


def get_client() -> MongoClient:
    """Return the process-wide MongoClient, creating it on first use.

    The lock stops two threads that hit the very first request at the same
    time from each building their own client.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = MongoClient(
                    settings.MONGO_URI,
                    tz_aware=True,
                    tzinfo=timezone.utc,
                    # Fail fast (5s) instead of the 30s default when Mongo is down.
                    serverSelectionTimeoutMS=5000,
                    appname="festflow",
                )
    return _client


def get_db(name: str | None = None) -> Database:
    """Return the application database (tests pass the test DB name)."""
    return get_client()[name or settings.MONGO_DB_NAME]


def ping() -> bool:
    """True if the server answers the `ping` command."""
    get_client().admin.command("ping")
    return True


def health_check() -> dict[str, Any]:
    """Collect server facts for the /health/ page.

    Errors are caught here and returned as data so that views never need to
    import PyMongo exceptions (layering rule: views -> services -> repositories).
    """
    try:
        ping()
        db = get_db()
        build_info = get_client().admin.command("buildInfo")
        collections = [
            {"name": name, "count": db[name].estimated_document_count()}
            for name in sorted(db.list_collection_names())
        ]
        return {
            "ok": True,
            "server_version": build_info.get("version"),
            "database": db.name,
            "collections": collections,
            "error": None,
        }
    except PyMongoError as exc:
        return {
            "ok": False,
            "server_version": None,
            "database": settings.MONGO_DB_NAME,
            "collections": [],
            "error": str(exc),
        }
