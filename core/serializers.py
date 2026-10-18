"""Helpers to turn MongoDB documents into template-friendly dicts."""
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId


def serialize(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a shallow copy of `doc` with a string `id` field added.

    Why: Django templates refuse to read attributes/keys starting with "_",
    so {{ event._id }} silently renders nothing. Templates use {{ event.id }}.
    `_id` is kept so repository/service code can still use the real ObjectId.
    """
    if doc is None:
        return None
    out = dict(doc)
    if "_id" in out:
        out["id"] = str(out["_id"])
    return out


def serialize_many(docs: Any) -> list[dict[str, Any]]:
    """serialize() every document from a cursor or list."""
    return [serialize(d) for d in docs]


def to_object_id(value: str | ObjectId) -> ObjectId | None:
    """Parse user input into an ObjectId; None if it is not a valid id."""
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None
