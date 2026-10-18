"""URL path converter so routes can capture MongoDB ObjectIds directly."""
from bson import ObjectId


class ObjectIdConverter:
    """Matches a 24-char lowercase hex string and turns it into an ObjectId.

    Registered as "oid" in festflow/urls.py, e.g. path("events/<oid:event_id>/").
    Anything that is not 24 hex chars never reaches the view — Django returns 404.
    """

    regex = "[0-9a-f]{24}"

    def to_python(self, value: str) -> ObjectId:
        return ObjectId(value)

    def to_url(self, value: ObjectId | str) -> str:
        return str(value)
