"""
Index definitions — the single source of truth (mirrored in queries/00_setup.js).

Every index has an explicit name so explain_report can drop and recreate it by
name, and so mongosh and Python always refer to the same index.
"""
from pymongo import ASCENDING, DESCENDING, TEXT, IndexModel

INDEXES: dict[str, list[IndexModel]] = {
    "venues": [
        IndexModel([("name", ASCENDING)], name="venues_name_unique", unique=True),
    ],
    "events": [
        # Event pages are addressed by slug, and two events must never share one.
        IndexModel([("slug", ASCENDING)], name="events_slug_unique", unique=True),
        # "What's on next" lists sort by start time.
        IndexModel([("startTime", ASCENDING)], name="events_startTime"),
        # Filter by type, then sort by time: equality field first, sort field second (ESR rule).
        IndexModel([("type", ASCENDING), ("startTime", ASCENDING)], name="events_type_startTime"),
        # Search box. A collection can have only ONE text index, so all fields go in it;
        # weights make a title match count 5x a description match.
        IndexModel(
            [("title", TEXT), ("description", TEXT), ("tags", TEXT)],
            name="events_text",
            weights={"title": 5, "tags": 3, "description": 1},
            default_language="english",
        ),
    ],
    "users": [
        IndexModel([("email", ASCENDING)], name="users_email_unique", unique=True),
        IndexModel([("role", ASCENDING)], name="users_role"),
    ],
    "bookings": [
        # Occupancy, refunds and dashboards all ask "bookings of event X with status Y".
        IndexModel([("eventId", ASCENDING), ("status", ASCENDING)], name="bookings_eventId_status"),
        # "My bookings", newest first.
        IndexModel([("userId", ASCENDING), ("bookedAt", DESCENDING)], name="bookings_userId_bookedAt"),
        # Multikey unique index: no ticket code may appear in two bookings. Check-in looks codes up here.
        IndexModel([("tickets.code", ASCENDING)], name="bookings_ticketCode_unique", unique=True),
    ],
}
