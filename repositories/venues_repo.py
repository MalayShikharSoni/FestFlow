"""
venues collection — every PyMongo call for venues lives here.

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


def _venues() -> Collection:
    return get_db().venues


def list_venues() -> list[dict[str, Any]]:
    """All venues A-Z (there are only a handful, so no pagination)."""
    return serialize_many(_venues().find({}).sort("name", 1))


def get_venue(venue_id: ObjectId) -> dict[str, Any] | None:
    return serialize(_venues().find_one({"_id": venue_id}))


def get_venue_by_name(name: str) -> dict[str, Any] | None:
    return serialize(_venues().find_one({"name": name}))


def get_venues_by_ids(venue_ids: list[ObjectId]) -> dict[ObjectId, dict[str, Any]]:
    """{_id: venue} for the given ids — one round trip instead of one per event."""
    cursor = _venues().find({"_id": {"$in": venue_ids}}, {"name": 1, "block": 1, "sections": 1})
    return {doc["_id"]: serialize(doc) for doc in cursor}


def insert_venue(doc: dict[str, Any]) -> ObjectId:
    with mongo_errors("venues"):
        return _venues().insert_one(doc).inserted_id


def update_venue(venue_id: ObjectId, fields: dict[str, Any]) -> bool:
    """$set only the given fields; True if the venue exists."""
    with mongo_errors("venues"):
        return _venues().update_one({"_id": venue_id}, {"$set": fields}).matched_count == 1


def upsert_venue_by_name(fields: dict[str, Any], now: datetime) -> bool:
    """Idempotent import: update the venue with this name, or create it. True if created.

    $setOnInsert only applies when the upsert inserts, so createdAt is set once
    and re-importing the same file never changes it.
    """
    name = fields["name"]
    rest = {k: v for k, v in fields.items() if k != "name"}
    with mongo_errors("venues"):
        result = _venues().update_one(
            {"name": name},
            {"$set": rest, "$setOnInsert": {"createdAt": now}},
            upsert=True,
        )
    return result.upserted_id is not None


def delete_venue(venue_id: ObjectId) -> bool:
    return _venues().delete_one({"_id": venue_id}).deleted_count == 1
