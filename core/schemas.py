"""
Collection validators — the single source of truth for document shape.

Each collection has two parts:
  * a $jsonSchema (X_SCHEMA): the *shape* of one document — types, required
    fields, enums, lengths, patterns, array sizes;
  * a list of named $expr rules (X_RULES): rules that compare fields of the SAME
    document (endTime > startTime, sold <= capacity, total == sum of prices).
    JSON Schema alone cannot compare one field with another, so these need $expr.
build_validator() combines them into
    {"$and": [{"$jsonSchema": ...}, {"$expr": rule1}, {"$expr": rule2}, ...]}
The rule names are not sent to MongoDB; core/exceptions.py uses them to turn
"clause 3 of the $and failed" into a readable message.

Rules involving OTHER documents or collections cannot live here (a validator only
ever sees the one document being written); they are enforced in services/ and
commented there.

All validators are applied with validationLevel "strict" (checked on every insert
and update) and validationAction "error" (bad writes are rejected, not just
logged) — see core/management/commands/init_db.py. queries/00_setup.js holds the
same validators written for mongosh.

Writing documents that pass:
  * Money and counters use bsonType ["int", "long"] (whole rupees). Python ints
    are sent as int32/int64 automatically; Python floats (1500.0) are doubles and
    are rejected. mongosh sends whole numbers that fit in 32 bits as int, but
    fractions and larger numbers as double — our scripts write NumberInt(...)
    to make the type explicit.
  * Dates must be real BSON dates (Python datetime / mongosh new Date()), never strings.
"""
from typing import Any

Schema = dict[str, Any]
Rule = tuple[str, dict[str, Any]]  # (human-readable name, $expr expression)

VALIDATION_LEVEL = "strict"
VALIDATION_ACTION = "error"

EVENT_TYPES = ["pro_show", "cultural", "workshop"]
EVENT_STATUSES = ["scheduled", "cancelled", "completed"]
FACILITIES = ["projector", "sound_system", "stage", "ac", "wheelchair_access", "green_room"]
ROLES = ["attendee", "volunteer", "organizer"]
DUTIES = ["gate", "stage", "help_desk", "logistics"]
BOOKING_STATUSES = ["confirmed", "cancelled", "refunded"]
PAYMENT_MODES = ["UPI", "CARD", "NETBANKING", "CASH", "FREE"]

# Reusable building blocks ---------------------------------------------------
INT = ["int", "long"]  # whole numbers only; doubles (and so rounding errors) are rejected


def _string(min_len: int = 1, max_len: int | None = None, **extra: Any) -> Schema:
    schema: Schema = {"bsonType": "string", "minLength": min_len}
    if max_len is not None:
        schema["maxLength"] = max_len
    schema.update(extra)
    return schema


def _int(minimum: int | None = None, maximum: int | None = None) -> Schema:
    schema: Schema = {"bsonType": INT}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


def _any_present(*fields: str) -> Schema:
    """Schema that matches if ANY of `fields` is present.

    Used as {"not": _any_present(...)} to forbid another event type's fields.
    """
    return {"anyOf": [{"required": [f]} for f in fields]}


def _all_unique(array_path: str) -> dict[str, Any]:
    """$expr: the values at `array_path` contain no duplicates.

    $setUnion removes duplicates, so the sizes match only if nothing was removed.
    JSON Schema's uniqueItems compares whole objects, not one key, so it cannot do this.
    """
    values = {"$ifNull": [array_path, []]}
    return {"$eq": [{"$size": {"$setUnion": [values, []]}}, {"$size": values}]}


def build_validator(json_schema: Schema, rules: list[Rule]) -> Schema:
    """{"$and": [{"$jsonSchema": ...}, {"$expr": ...}, ...]} — clause 0 is always the schema."""
    return {"$and": [{"$jsonSchema": json_schema}] + [{"$expr": expr} for _, expr in rules]}


# ---------------------------------------------------------------------------
# venues
#
# EMBED sections (1:few): a venue has a handful of sections, they are bounded,
# and they are always read together with the venue.
# ---------------------------------------------------------------------------
VENUES_SCHEMA: Schema = {
    "bsonType": "object",
    "title": "venues",
    "required": ["name", "block", "sections", "facilities", "createdAt"],
    "additionalProperties": False,
    "properties": {
        "_id": {"bsonType": "objectId"},
        "name": _string(3, 80),
        "block": _string(1, 40),
        "sections": {
            "bsonType": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {
                "bsonType": "object",
                "required": ["name", "capacity"],
                "additionalProperties": False,
                "properties": {
                    "name": _string(1, 40),
                    "capacity": _int(minimum=1),
                },
            },
        },
        "facilities": {
            "bsonType": "array",
            "uniqueItems": True,
            "items": {"enum": FACILITIES},
        },
        "createdAt": {"bsonType": "date"},
    },
}

VENUES_RULES: list[Rule] = [
    # Event tiers point at a section *by name*, so names must be unique per venue.
    ("section names must be unique within a venue", _all_unique("$sections.name")),
]


# ---------------------------------------------------------------------------
# events — polymorphic: one collection, shape depends on `type`
#
# EMBED ticketTiers: the stock counter (`sold`) must sit in the SAME document as
# its `capacity`, so one atomic find_one_and_update can check and claim seats.
# This is the headline design decision of the project.
# REFERENCE venue (N:1): many events share a venue and venues are edited on
# their own, so we store venueId instead of copying the venue in.
# ---------------------------------------------------------------------------
def _tier_schema(min_price: int) -> Schema:
    return {
        "bsonType": "object",
        "required": ["tierId", "name", "section", "price", "capacity", "sold"],
        "additionalProperties": False,
        "properties": {
            "tierId": _string(2, 10, pattern="^[A-Z]{2,10}$"),
            "name": _string(1, 40),
            "section": _string(1, 40),
            "price": _int(minimum=min_price),
            "capacity": _int(minimum=1),
            "sold": _int(minimum=0),
        },
    }


TICKETED_ONLY_FIELDS = ("ticketTiers", "artist")
WORKSHOP_ONLY_FIELDS = ("seatLimit", "seatsTaken", "instructor", "prerequisites")

PRO_SHOW_SHAPE: Schema = {
    "properties": {
        "type": {"enum": ["pro_show"]},
        "ticketTiers": {"bsonType": "array", "minItems": 1, "maxItems": 5,
                        "items": _tier_schema(min_price=1)},
        "artist": _string(1, 80),
    },
    "required": ["ticketTiers", "artist"],
    "not": _any_present(*WORKSHOP_ONLY_FIELDS),
}

CULTURAL_SHAPE: Schema = {
    "properties": {
        "type": {"enum": ["cultural"]},
        # price >= 0: free passes are allowed for cultural events.
        "ticketTiers": {"bsonType": "array", "minItems": 1, "maxItems": 3,
                        "items": _tier_schema(min_price=0)},
    },
    "required": ["ticketTiers"],
    "not": _any_present("artist", *WORKSHOP_ONLY_FIELDS),
}

WORKSHOP_SHAPE: Schema = {
    "properties": {
        "type": {"enum": ["workshop"]},
        "seatLimit": _int(minimum=1, maximum=200),
        "seatsTaken": _int(minimum=0),
        "instructor": _string(1, 80),
        "prerequisites": {"bsonType": "array", "items": _string(1, 120)},
    },
    "required": ["seatLimit", "seatsTaken", "instructor", "prerequisites"],
    "not": _any_present(*TICKETED_ONLY_FIELDS),
}

EVENTS_SCHEMA: Schema = {
    "bsonType": "object",
    "title": "events",
    "required": ["title", "slug", "description", "type", "venueId", "startTime",
                 "endTime", "status", "tags", "createdAt", "updatedAt"],
    # additionalProperties only sees properties listed at THIS level, so the
    # type-specific fields are listed here too (loosely) and tightened in oneOf.
    "additionalProperties": False,
    "properties": {
        "_id": {"bsonType": "objectId"},
        "title": _string(3, 120),
        "slug": _string(1, 120, pattern="^[a-z0-9-]+$"),
        "description": _string(10, 2000),
        "type": {"enum": EVENT_TYPES},
        "venueId": {"bsonType": "objectId"},
        "startTime": {"bsonType": "date"},
        "endTime": {"bsonType": "date"},
        "status": {"enum": EVENT_STATUSES},
        "tags": {"bsonType": "array", "items": _string(1, 30)},
        "createdAt": {"bsonType": "date"},
        "updatedAt": {"bsonType": "date"},
        "ticketTiers": {"bsonType": "array"},
        "artist": {"bsonType": "string"},
        "seatLimit": {"bsonType": INT},
        "seatsTaken": {"bsonType": INT},
        "instructor": {"bsonType": "string"},
        "prerequisites": {"bsonType": "array"},
    },
    # Exactly one branch can match, because each branch pins `type`.
    "oneOf": [PRO_SHOW_SHAPE, CULTURAL_SHAPE, WORKSHOP_SHAPE],
}

EVENTS_RULES: list[Rule] = [
    ("endTime must be after startTime", {"$gt": ["$endTime", "$startTime"]}),
    # Safety net behind atomic booking: no tier can ever be oversold.
    ("every tier must have sold <= capacity", {"$allElementsTrue": [{"$map": {
        "input": {"$ifNull": ["$ticketTiers", []]},
        "as": "t",
        "in": {"$lte": ["$$t.sold", "$$t.capacity"]},
    }}]}),
    # Same safety net for workshops.
    ("workshop seatsTaken must be <= seatLimit", {"$or": [
        {"$ne": ["$type", "workshop"]},
        {"$lte": ["$seatsTaken", "$seatLimit"]},
    ]}),
    # Booking updates "ticketTiers.$" matched by tierId, so tierIds must be unique.
    ("tierIds must be unique within an event", _all_unique("$ticketTiers.tierId")),
]


# ---------------------------------------------------------------------------
# users
#
# EMBED shifts (1:few): a volunteer has a handful of shifts over 5 days and
# they are always shown with the volunteer.
# ---------------------------------------------------------------------------
USERS_SCHEMA: Schema = {
    "bsonType": "object",
    "title": "users",
    "required": ["name", "email", "phone", "role", "passwordHash", "college",
                 "shifts", "createdAt"],
    "additionalProperties": False,
    "properties": {
        "_id": {"bsonType": "objectId"},
        "name": _string(2, 80),
        # Lowercase-only pattern: this is what enforces "email is lowercase".
        "email": _string(5, 120, pattern="^[a-z0-9._%+-]+@[a-z0-9.-]+\\.[a-z]{2,}$"),
        # Indian mobile: starts with 6-9, 10 digits.
        "phone": _string(10, 10, pattern="^[6-9][0-9]{9}$"),
        "role": {"enum": ROLES},
        "passwordHash": _string(1, 200),
        "college": _string(2, 120),
        "regNo": _string(8, 8, pattern="^[0-9]{8}$"),  # optional: not in `required`
        "shifts": {
            "bsonType": "array",
            "maxItems": 20,
            "items": {
                "bsonType": "object",
                "required": ["eventId", "duty", "start", "end"],
                "additionalProperties": False,
                "properties": {
                    "eventId": {"bsonType": "objectId"},
                    "duty": {"enum": DUTIES},
                    "start": {"bsonType": "date"},
                    "end": {"bsonType": "date"},
                },
            },
        },
        "createdAt": {"bsonType": "date"},
    },
}

USERS_RULES: list[Rule] = [
    ("only volunteers may have shifts", {"$or": [
        {"$eq": ["$role", "volunteer"]},
        {"$eq": [{"$size": {"$ifNull": ["$shifts", []]}}, 0]},
    ]}),
    ("every shift must end after it starts", {"$allElementsTrue": [{"$map": {
        "input": {"$ifNull": ["$shifts", []]},
        "as": "s",
        "in": {"$gt": ["$$s.end", "$$s.start"]},
    }}]}),
]


# ---------------------------------------------------------------------------
# bookings
#
# REFERENCE user and event (N:M resolved through this collection): an event has
# thousands of bookings, so embedding them in events (or users) would create
# unbounded arrays and huge documents; bookings are also queried on their own.
# eventType is DENORMALIZED from events.type: an event's type never changes, and
# copying it lets analytics group by type without a $lookup.
# ---------------------------------------------------------------------------
BOOKINGS_SCHEMA: Schema = {
    "bsonType": "object",
    "title": "bookings",
    "required": ["userId", "eventId", "eventType", "status", "paymentMode",
                 "tickets", "totalAmount", "bookedAt", "cancelledAt"],
    "additionalProperties": False,
    "properties": {
        "_id": {"bsonType": "objectId"},
        "userId": {"bsonType": "objectId"},
        "eventId": {"bsonType": "objectId"},
        "eventType": {"enum": EVENT_TYPES},
        "status": {"enum": BOOKING_STATUSES},
        "paymentMode": {"enum": PAYMENT_MODES},
        "tickets": {
            "bsonType": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": {
                "bsonType": "object",
                "required": ["code", "tierId", "price", "checkedInAt"],
                "additionalProperties": False,
                "properties": {
                    "code": _string(12, 12, pattern="^SPX-[A-Z0-9]{8}$"),
                    "tierId": {"bsonType": ["string", "null"]},
                    "price": _int(minimum=0),
                    "checkedInAt": {"bsonType": ["date", "null"]},
                },
            },
        },
        "totalAmount": _int(minimum=0),
        "bookedAt": {"bsonType": "date"},
        "cancelledAt": {"bsonType": ["date", "null"]},
    },
}

BOOKINGS_RULES: list[Rule] = [
    ("totalAmount must equal the sum of ticket prices",
     {"$eq": ["$totalAmount", {"$sum": "$tickets.price"}]}),
    ("a workshop booking has exactly one ticket with tierId null", {"$or": [
        {"$ne": ["$eventType", "workshop"]},
        {"$and": [
            {"$eq": [{"$size": "$tickets"}, 1]},
            {"$eq": [{"$arrayElemAt": ["$tickets.tierId", 0]}, None]},
        ]},
    ]}),
    ("tickets for a ticketed event must each name a tier", {"$or": [
        {"$eq": ["$eventType", "workshop"]},
        {"$not": [{"$in": [None, "$tickets.tierId"]}]},
    ]}),
    # So cancelling a booking releases seats from exactly one tier.
    ("all tickets in a booking must share one tier",
     {"$eq": [{"$size": {"$setUnion": ["$tickets.tierId", []]}}, 1]}),
    # A unique multikey index stops two BOOKINGS sharing a code, but NOT the same
    # code twice inside one booking's array — this rule covers that gap.
    ("ticket codes must be unique within a booking", _all_unique("$tickets.code")),
    ("cancelledAt is set exactly when status is not confirmed", {"$eq": [
        {"$eq": ["$status", "confirmed"]},
        {"$eq": [{"$ifNull": ["$cancelledAt", None]}, None]},
    ]}),
]


# ---------------------------------------------------------------------------
RULES: dict[str, list[Rule]] = {
    "venues": VENUES_RULES,
    "events": EVENTS_RULES,
    "users": USERS_RULES,
    "bookings": BOOKINGS_RULES,
}

VALIDATORS: dict[str, Schema] = {
    "venues": build_validator(VENUES_SCHEMA, VENUES_RULES),
    "events": build_validator(EVENTS_SCHEMA, EVENTS_RULES),
    "users": build_validator(USERS_SCHEMA, USERS_RULES),
    "bookings": build_validator(BOOKINGS_SCHEMA, BOOKINGS_RULES),
}


def rule_name(collection: str, and_clause_index: int) -> str:
    """Name of the $expr rule at position `and_clause_index` of the $and (0 is the schema)."""
    rules = RULES.get(collection, [])
    i = and_clause_index - 1
    return rules[i][0] if 0 <= i < len(rules) else f"rule #{and_clause_index}"
