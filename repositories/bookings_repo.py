"""
bookings collection — every PyMongo call for bookings lives here.

Each function has a mongosh twin with the same name in queries/02_crud.js.
Reads return serialize()d dicts (with a string "id" for templates).
"""
from datetime import datetime
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.collection import Collection

from core.db import get_db
from core.exceptions import mongo_errors
from core.serializers import serialize, serialize_many

# A booking with no ticket checked in: no element of `tickets` has a non-null checkedInAt.
NO_TICKET_CHECKED_IN = {"tickets": {"$not": {"$elemMatch": {"checkedInAt": {"$ne": None}}}}}


def _bookings() -> Collection:
    return get_db().bookings


# --- Reads -------------------------------------------------------------------
def get_booking(booking_id: ObjectId) -> dict[str, Any] | None:
    return serialize(_bookings().find_one({"_id": booking_id}))


def find_by_ticket_code(code: str) -> dict[str, Any] | None:
    """Uses the unique multikey index on tickets.code."""
    return serialize(_bookings().find_one({"tickets.code": code}))


def list_for_user(user_id: ObjectId, skip: int = 0, limit: int = 50) -> list[dict[str, Any]]:
    """'My bookings', newest first — served by the {userId: 1, bookedAt: -1} index."""
    cursor = _bookings().find({"userId": user_id}).sort("bookedAt", -1).skip(skip).limit(limit)
    return serialize_many(cursor)


def has_confirmed_booking(user_id: ObjectId, event_id: ObjectId) -> bool:
    return _bookings().count_documents(
        {"userId": user_id, "eventId": event_id, "status": "confirmed"}, limit=1) == 1


def count_for_event(event_id: ObjectId) -> int:
    """Every booking of the event, any status (used before a hard delete)."""
    return _bookings().count_documents({"eventId": event_id})


def distinct_user_ids() -> list[ObjectId]:
    """Everyone who has ever booked anything."""
    return _bookings().distinct("userId")


# --- Writes ------------------------------------------------------------------
def insert_booking(doc: dict[str, Any]) -> ObjectId:
    with mongo_errors("bookings"):
        return _bookings().insert_one(doc).inserted_id


def cancel_booking(booking_id: ObjectId, user_id: ObjectId, new_status: str, now: datetime) -> bool:
    """confirmed -> cancelled/refunded, only if still confirmed and nothing is checked in.

    The conditions sit in the filter, so a gate scan happening at the same moment
    cannot sneak in between our checks and this write: one of the two wins.
    """
    with mongo_errors("bookings"):
        result = _bookings().update_one(
            {"_id": booking_id, "userId": user_id, "status": "confirmed", **NO_TICKET_CHECKED_IN},
            {"$set": {"status": new_status, "cancelledAt": now}},
        )
    return result.modified_count == 1


def check_in_ticket(code: str, now: datetime) -> dict[str, Any] | None:
    """Atomically stamp checkedInAt on one ticket; None if not possible.

    The filter matches only a CONFIRMED booking holding this code with
    checkedInAt still null, so scanning the same code twice can never count twice:
    the second scan no longer matches. "$" targets the ticket $elemMatch found.
    """
    with mongo_errors("bookings"):
        doc = _bookings().find_one_and_update(
            {"status": "confirmed", "tickets": {"$elemMatch": {"code": code, "checkedInAt": None}}},
            {"$set": {"tickets.$.checkedInAt": now}},
            return_document=ReturnDocument.AFTER,
        )
    return serialize(doc)


def refund_all_for_event(event_id: ObjectId, now: datetime, booking_id: ObjectId | None = None) -> int:
    """Cancel every confirmed booking of an event: "refunded" if paid, "cancelled" if free.

    The update is a PIPELINE (a list of stages), which lets $set compute the new
    status from the document's own totalAmount with $cond, in one update_many.
    `booking_id` narrows it to a single booking (used for a race in booking_service).
    """
    query: dict[str, Any] = {"eventId": event_id, "status": "confirmed"}
    if booking_id:
        query["_id"] = booking_id
    with mongo_errors("bookings"):
        result = _bookings().update_many(query, [
            {"$set": {
                "status": {"$cond": [{"$gt": ["$totalAmount", 0]}, "refunded", "cancelled"]},
                "cancelledAt": now,
            }},
        ])
    return result.modified_count
