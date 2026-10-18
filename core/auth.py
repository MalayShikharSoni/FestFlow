"""
Session-based auth against the MongoDB `users` collection.

We do not install django.contrib.auth (its User model needs a SQL table), so
login state is just two keys in the signed-cookie session: user_id and role.
Phase 0 provides the session plumbing and decorators; the users lookup that
checks passwords lives in services/auth_service.py (Phase 3).
"""
from functools import wraps
from typing import Any, Callable

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect
from django.utils.functional import SimpleLazyObject

SESSION_USER_ID = "user_id"
SESSION_ROLE = "role"

ViewFunc = Callable[..., HttpResponse]


def login_session(request: HttpRequest, user_id: str, role: str) -> None:
    """Mark the request's session as logged in.

    cycle_key() issues a fresh session id to prevent session fixation.
    """
    request.session.cycle_key()
    request.session[SESSION_USER_ID] = user_id
    request.session[SESSION_ROLE] = role


def logout_session(request: HttpRequest) -> None:
    """Forget everything in the session (the cookie is replaced)."""
    request.session.flush()


def get_session_user(request: HttpRequest) -> dict[str, str] | None:
    """Return {"id", "role"} from the session, or None if not logged in."""
    user_id = request.session.get(SESSION_USER_ID)
    if not user_id:
        return None
    return {"id": user_id, "role": request.session.get(SESSION_ROLE, "")}


def login_required_mongo(view: ViewFunc) -> ViewFunc:
    """Redirect anonymous users to the login page, remembering where they were."""

    @wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if get_session_user(request) is None:
            messages.info(request, "Please log in first.")
            return redirect(f"/login/?next={request.path}")
        return view(request, *args, **kwargs)

    return wrapper


def role_required(*roles: str) -> Callable[[ViewFunc], ViewFunc]:
    """Allow only users whose session role is one of `roles` (implies login)."""

    def decorator(view: ViewFunc) -> ViewFunc:
        @wraps(view)
        @login_required_mongo
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            user = get_session_user(request)
            if user is None or user["role"] not in roles:
                return HttpResponseForbidden("You do not have access to this page.")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def current_user(request: HttpRequest) -> dict[str, Any]:
    """Template context processor: exposes {{ current_user }} on every page.

    Replaces django.contrib.auth's `user` context processor. The session holds
    only id and role; the name is looked up LAZILY (SimpleLazyObject), i.e. only
    on pages whose template actually uses {{ current_user.name }}.
    """
    session_user = get_session_user(request)
    if session_user is None:
        return {"current_user": None}

    def load() -> dict[str, Any]:
        from bson import ObjectId

        from services.auth_service import get_user  # imported here: core must not depend on services at import time
        user = get_user(ObjectId(session_user["id"])) or {}
        return {**session_user, "name": user.get("name", ""), "email": user.get("email", "")}

    return {"current_user": SimpleLazyObject(load)}
