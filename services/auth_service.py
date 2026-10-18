"""
Registration and login against the MongoDB `users` collection.

Passwords are hashed with Django's make_password (PBKDF2) — it works without
django.contrib.auth installed (proved in Phase 0). The session itself is handled
by core/auth.py; this module only answers "who is this, and is the password right?".
"""
from datetime import timedelta
from typing import Any

from bson import ObjectId
from django.contrib.auth.hashers import check_password, make_password

from core import clock
from repositories import bookings_repo, users_repo


def register(name: str, email: str, phone: str, password: str, college: str,
             reg_no: str | None = None) -> dict[str, Any]:
    """Create an attendee account. Raises DuplicateError if the email is taken.

    We do not check "is this email free?" first: the unique index answers that
    atomically, and a read-then-insert would race with a parallel sign-up.
    Field formats (email pattern, phone pattern, lengths) are checked by the
    validator, which raises ValidationFailed.
    """
    doc: dict[str, Any] = {
        "name": name.strip(),
        "email": email.strip().lower(),  # the validator only accepts lowercase
        "phone": phone.strip(),
        "role": "attendee",
        "passwordHash": make_password(password),
        "college": college.strip(),
        "shifts": [],
        "createdAt": clock.now(),
    }
    if reg_no:
        doc["regNo"] = reg_no.strip()
    user_id = users_repo.insert_user(doc)
    return users_repo.get_user(user_id)


def authenticate(email: str, password: str) -> dict[str, Any] | None:
    """The user (without passwordHash) if the credentials are right, else None."""
    user = users_repo.get_user_with_password(email.strip().lower())
    if user is None:
        # Hash anyway so "no such email" takes as long as "wrong password";
        # otherwise response times would reveal which emails are registered.
        make_password(password)
        return None
    if not check_password(password, user["passwordHash"]):
        return None
    user.pop("passwordHash")
    return user


def get_user(user_id: ObjectId) -> dict[str, Any] | None:
    return users_repo.get_user(user_id)


def cleanup_inactive_attendees(older_than_days: int = 30) -> int:
    """Delete attendee accounts older than N days that never booked anything."""
    cutoff = clock.now() - timedelta(days=older_than_days)
    return users_repo.delete_inactive_attendees(cutoff, bookings_repo.distinct_user_ids())
