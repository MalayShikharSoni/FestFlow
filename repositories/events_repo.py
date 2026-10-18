"""
events collection — every PyMongo call for events lives here.

Each function has a mongosh twin with the same name in queries/02_crud.js.
Reads return serialize()d dicts (with a string "id" for templates).

The seat-claiming functions are the heart of the project: the stock counter
lives inside the event document, so one single-document atomic update can both
CHECK that seats are left and TAKE them. Two buyers can never get the same seat.
"""
from datetime import datetime
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.collection import Collection

from core.db import get_db
from core.exceptions import mongo_errors
from core.serializers import serialize, serialize_many

# What list pages need; leaves out the long description.
LIST_PROJECTION = {
    "title": 1, "slug": 1, "type": 1, "venueId": 1, "startTime": 1, "endTime": 1,
    "status": 1, "tags": 1, "artist": 1, "ticketTiers": 1, "seatLimit": 1, "seatsTaken": 1,
}


def _events() -> Collection:
    return get_db().events


# --- Reads -------------------------------------------------------------------
def build_list_filter(event_type: str | None = None, starts_from: datetime | None = None,
                      starts_before: datetime | None = None, include_cancelled: bool = False) -> dict[str, Any]:
    """Filter for the event list: optional type, optional day window, cancelled hidden by default."""
    query: dict[str, Any] = {}
    if event_type:
        query["type"] = event_type
    if starts_from or starts_before:
        query["startTime"] = {}
        if starts_from:
            query["startTime"]["$gte"] = starts_from
        if starts_before:
            query["startTime"]["$lt"] = starts_before
    if not include_cancelled:
        query["status"] = {"$ne": "cancelled"}
    return query


def list_events(query: dict[str, Any], skip: int, limit: int) -> list[dict[str, Any]]:
    """find with filter + projection + sort + skip/limit (pagination)."""
    cursor = _events().find(query, LIST_PROJECTION).sort("startTime", 1).skip(skip).limit(limit)
    return serialize_many(cursor)


def count_events(query: dict[str, Any]) -> int:
    """Total for the pager. count_documents runs the filter; estimated_document_count would ignore it."""
    return _events().count_documents(query)


def search_events(text: str, query: dict[str, Any], skip: int, limit: int) -> list[dict[str, Any]]:
    """Full-text search on the weighted text index, best match first.

    {"$meta": "textScore"} exposes how well each document matched (a title hit
    weighs 5x a description hit, per the index weights).
    """
    text_query = {**query, "$text": {"$search": text}}
    projection = {**LIST_PROJECTION, "score": {"$meta": "textScore"}}
    cursor = (_events().find(text_query, projection)
              .sort([("score", {"$meta": "textScore"}), ("startTime", 1)])
              .skip(skip).limit(limit))
    return serialize_many(cursor)


def count_search(text: str, query: dict[str, Any]) -> int:
    return _events().count_documents({**query, "$text": {"$search": text}})


def get_event(event_id: ObjectId) -> dict[str, Any] | None:
    return serialize(_events().find_one({"_id": event_id}))


def get_event_by_slug(slug: str) -> dict[str, Any] | None:
    return serialize(_events().find_one({"slug": slug}))


def get_events_by_ids(event_ids: list[ObjectId]) -> dict[ObjectId, dict[str, Any]]:
    cursor = _events().find({"_id": {"$in": event_ids}}, LIST_PROJECTION)
    return {doc["_id"]: serialize(doc) for doc in cursor}


def list_all_events() -> list[dict[str, Any]]:
    """Organizer table: every event (only ~25), soonest first."""
    return serialize_many(_events().find({}, LIST_PROJECTION).sort("startTime", 1))


def list_start_times() -> list[datetime]:
    """Distinct start times of visible events, for the "day" filter."""
    return _events().distinct("startTime", {"status": {"$ne": "cancelled"}})


def count_overlapping(venue_id: ObjectId, start: datetime, end: datetime,
                      exclude_id: ObjectId | None = None) -> int:
    """Non-cancelled events at this venue whose time range overlaps [start, end).

    Two ranges overlap when each one starts before the other ends.
    """
    query: dict[str, Any] = {
        "venueId": venue_id,
        "status": {"$ne": "cancelled"},
        "startTime": {"$lt": end},
        "endTime": {"$gt": start},
    }
    if exclude_id:
        query["_id"] = {"$ne": exclude_id}
    return _events().count_documents(query)


def count_for_venue(venue_id: ObjectId) -> int:
    return _events().count_documents({"venueId": venue_id})


def list_active_for_venue(venue_id: ObjectId) -> list[dict[str, Any]]:
    """Non-cancelled events at a venue, with their tiers (to re-check section capacities)."""
    cursor = _events().find({"venueId": venue_id, "status": {"$ne": "cancelled"}}, {"title": 1, "ticketTiers": 1})
    return serialize_many(cursor)


# --- Writes ------------------------------------------------------------------
def insert_event(doc: dict[str, Any]) -> ObjectId:
    with mongo_errors("events"):
        return _events().insert_one(doc).inserted_id


def update_event_fields(event_id: ObjectId, fields: dict[str, Any], now: datetime) -> bool:
    with mongo_errors("events"):
        result = _events().update_one({"_id": event_id}, {"$set": {**fields, "updatedAt": now}})
    return result.matched_count == 1


def claim_tier_seats(event_id: ObjectId, tier_id: str, qty: int, capacity: int,
                     now: datetime) -> dict[str, Any] | None:
    """Atomically take `qty` seats from one tier; None if they are not available.

    The filter only matches if the event is still open AND this tier has
    sold <= capacity - qty, and the $inc runs in the same single-document
    operation, so no other request can slip in between the check and the write.
    The positional "$" means "the array element the $elemMatch matched".

    Why `capacity` is passed in: inside $elemMatch we cannot compare one field of
    the element with another (sold vs capacity), so the caller reads the tier's
    capacity first. If an organizer lowered the capacity in the meantime, the
    validator's "sold <= capacity" rule rejects the $inc — the safety net.
    """
    with mongo_errors("events"):
        doc = _events().find_one_and_update(
            {
                "_id": event_id,
                "status": "scheduled",
                "startTime": {"$gt": now},
                "ticketTiers": {"$elemMatch": {"tierId": tier_id, "sold": {"$lte": capacity - qty}}},
            },
            {"$inc": {"ticketTiers.$.sold": qty}, "$set": {"updatedAt": now}},
            return_document=ReturnDocument.AFTER,
        )
    return serialize(doc)


def release_tier_seats(event_id: ObjectId, tier_id: str, qty: int, now: datetime) -> bool:
    """Give seats back (cancellation or a failed booking insert)."""
    with mongo_errors("events"):
        result = _events().update_one(
            {"_id": event_id, "ticketTiers.tierId": tier_id},
            {"$inc": {"ticketTiers.$.sold": -qty}, "$set": {"updatedAt": now}},
        )
    return result.modified_count == 1


def claim_workshop_seat(event_id: ObjectId, now: datetime) -> dict[str, Any] | None:
    """Atomically take one workshop seat; None if it is full or closed.

    seatsTaken and seatLimit are top-level fields, so here $expr CAN compare
    them directly — no need to read the limit first, unlike ticket tiers.
    """
    with mongo_errors("events"):
        doc = _events().find_one_and_update(
            {
                "_id": event_id,
                "type": "workshop",
                "status": "scheduled",
                "startTime": {"$gt": now},
                "$expr": {"$lt": ["$seatsTaken", "$seatLimit"]},
            },
            {"$inc": {"seatsTaken": 1}, "$set": {"updatedAt": now}},
            return_document=ReturnDocument.AFTER,
        )
    return serialize(doc)


def release_workshop_seat(event_id: ObjectId, now: datetime) -> bool:
    with mongo_errors("events"):
        result = _events().update_one(
            {"_id": event_id, "seatsTaken": {"$gt": 0}},
            {"$inc": {"seatsTaken": -1}, "$set": {"updatedAt": now}},
        )
    return result.modified_count == 1


def set_tier_price(event_id: ObjectId, tier_id: str, price: int, now: datetime) -> bool:
    """Change one tier's price with arrayFilters: $[t] = every element where t.tierId matches."""
    with mongo_errors("events"):
        result = _events().update_one(
            {"_id": event_id, "ticketTiers.tierId": tier_id},
            {"$set": {"ticketTiers.$[t].price": price, "updatedAt": now}},
            array_filters=[{"t.tierId": tier_id}],
        )
    return result.matched_count == 1


def set_tier_capacity(event_id: ObjectId, tier_id: str, capacity: int, now: datetime) -> bool:
    """Change a tier's capacity, but never below what is already sold (False if it would be).

    The guard is in the FILTER, so check and write are one atomic step even if a
    booking is happening at the same moment.
    """
    with mongo_errors("events"):
        result = _events().update_one(
            {"_id": event_id,
             "ticketTiers": {"$elemMatch": {"tierId": tier_id, "sold": {"$lte": capacity}}}},
            {"$set": {"ticketTiers.$[t].capacity": capacity, "updatedAt": now}},
            array_filters=[{"t.tierId": tier_id}],
        )
    return result.matched_count == 1


def add_tier(event_id: ObjectId, tier: dict[str, Any], now: datetime) -> bool:
    """$push a new tier; False if the tierId already exists in this event."""
    with mongo_errors("events"):
        result = _events().update_one(
            {"_id": event_id, "ticketTiers.tierId": {"$ne": tier["tierId"]}},
            {"$push": {"ticketTiers": tier}, "$set": {"updatedAt": now}},
        )
    return result.matched_count == 1


def remove_tier(event_id: ObjectId, tier_id: str, now: datetime) -> bool:
    """$pull a tier — only allowed while it has sold nothing (False otherwise)."""
    with mongo_errors("events"):
        result = _events().update_one(
            {"_id": event_id, "ticketTiers": {"$elemMatch": {"tierId": tier_id, "sold": 0}}},
            {"$pull": {"ticketTiers": {"tierId": tier_id}}, "$set": {"updatedAt": now}},
        )
    return result.matched_count == 1


def set_seat_limit(event_id: ObjectId, seat_limit: int, now: datetime) -> bool:
    """Change a workshop's seatLimit, never below seatsTaken (False if it would be)."""
    with mongo_errors("events"):
        result = _events().update_one(
            {"_id": event_id, "seatsTaken": {"$lte": seat_limit}},
            {"$set": {"seatLimit": seat_limit, "updatedAt": now}},
        )
    return result.matched_count == 1


def mark_cancelled(event_id: ObjectId, now: datetime) -> bool:
    """scheduled -> cancelled. Once this runs, claim_* can no longer match the event."""
    with mongo_errors("events"):
        result = _events().update_one(
            {"_id": event_id, "status": "scheduled"},
            {"$set": {"status": "cancelled", "updatedAt": now}},
        )
    return result.modified_count == 1


def reset_stock(event_id: ObjectId, is_workshop: bool, now: datetime) -> None:
    """Zero the stock counters after every booking was refunded.

    "ticketTiers.$[].sold" uses the all-positional operator $[]: every element.
    """
    field = {"seatsTaken": 0} if is_workshop else {"ticketTiers.$[].sold": 0}
    with mongo_errors("events"):
        _events().update_one({"_id": event_id}, {"$set": {**field, "updatedAt": now}})


def delete_event(event_id: ObjectId) -> bool:
    return _events().delete_one({"_id": event_id}).deleted_count == 1
