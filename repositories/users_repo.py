"""
users collection — every PyMongo call for users lives here.

Each function has a mongosh twin with the same name in queries/02_crud.js.
Reads return serialize()d dicts (with a string "id" for templates).
"""
from datetime import datetime
from typing import Any

from bson import ObjectId
from pymongo.collection import Collection

from core.db import get_db
from core.exceptions import mongo_errors
from core.serializers import serialize, serialize_many

# Never send password hashes to templates.
PUBLIC_PROJECTION = {"passwordHash": 0}


def _users() -> Collection:
    return get_db().users


# --- Reads -------------------------------------------------------------------
def get_user(user_id: ObjectId) -> dict[str, Any] | None:
    return serialize(_users().find_one({"_id": user_id}, PUBLIC_PROJECTION))


def get_user_with_password(email: str) -> dict[str, Any] | None:
    """The only read that includes passwordHash — used by login."""
    return serialize(_users().find_one({"email": email}))


def get_users_by_ids(user_ids: list[ObjectId]) -> dict[ObjectId, dict[str, Any]]:
    cursor = _users().find({"_id": {"$in": user_ids}}, {"name": 1, "email": 1, "college": 1})
    return {doc["_id"]: serialize(doc) for doc in cursor}


def list_volunteers() -> list[dict[str, Any]]:
    """Volunteers A-Z with their shifts — served by the {role: 1} index."""
    cursor = _users().find({"role": "volunteer"}, {"name": 1, "email": 1, "phone": 1, "shifts": 1}).sort("name", 1)
    return serialize_many(cursor)


# --- Writes ------------------------------------------------------------------
def insert_user(doc: dict[str, Any]) -> ObjectId:
    """Raises DuplicateError if the email is taken (unique index)."""
    with mongo_errors("users"):
        return _users().insert_one(doc).inserted_id


def push_shift(user_id: ObjectId, shift: dict[str, Any]) -> bool:
    """$push a shift unless it overlaps one the volunteer already has (False then).

    The overlap test sits in the filter: "no existing shift starts before the new
    one ends AND ends after it starts". So two organizers assigning clashing
    shifts at the same moment cannot both succeed.
    """
    with mongo_errors("users"):
        result = _users().update_one(
            {
                "_id": user_id,
                "role": "volunteer",
                "shifts": {"$not": {"$elemMatch": {"start": {"$lt": shift["end"]}, "end": {"$gt": shift["start"]}}}},
            },
            {"$push": {"shifts": shift}},
        )
    return result.modified_count == 1


def pull_shift(user_id: ObjectId, event_id: ObjectId, start: datetime) -> bool:
    """$pull removes every array element matching the condition (here: one shift)."""
    with mongo_errors("users"):
        result = _users().update_one(
            {"_id": user_id},
            {"$pull": {"shifts": {"eventId": event_id, "start": start}}},
        )
    return result.modified_count == 1


def pull_shifts_for_event(event_id: ObjectId) -> int:
    """update_many + $pull: drop this event's shifts from every volunteer (event deleted)."""
    with mongo_errors("users"):
        result = _users().update_many(
            {"shifts.eventId": event_id},
            {"$pull": {"shifts": {"eventId": event_id}}},
        )
    return result.modified_count


def delete_inactive_attendees(registered_before: datetime, active_user_ids: list[ObjectId]) -> int:
    """delete_many: attendee accounts older than the cutoff that have never booked.

    Safe for referential integrity because no booking points at them.
    """
    result = _users().delete_many({
        "role": "attendee",
        "createdAt": {"$lt": registered_before},
        "_id": {"$nin": active_user_ids},
    })
    return result.deleted_count
