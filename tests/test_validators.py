"""
Validator rejections: raw PyMongo writes that MongoDB itself must refuse.

No service code is involved — this proves the rules live in the database.
Every case asserts WriteError with code 121 (DocumentValidationFailure).
"""
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from bson import ObjectId
from pymongo.database import Database
from pymongo.errors import WriteError

from core.exceptions import ValidationFailed, describe_err_info
from tests import factories as f

DOCUMENT_VALIDATION_FAILURE = 121


def _event(**overrides: Any) -> dict[str, Any]:
    return f.event_doc(ObjectId(), **overrides)


def _workshop(**overrides: Any) -> dict[str, Any]:
    return f.event_doc(ObjectId(), type_="workshop", **overrides)


def _booking(**overrides: Any) -> dict[str, Any]:
    doc = f.booking_doc(ObjectId(), ObjectId(), [f.ticket(), f.ticket()])
    return {**doc, **overrides}


def _without(doc: dict[str, Any], field: str) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k != field}


REJECTED: list[tuple[str, str, Callable[[], dict[str, Any]]]] = [
    # (case, collection, builder)
    ("event type not in enum", "events", lambda: _event(type="concert")),
    ("negative ticket price", "events", lambda: _event(ticketTiers=[f.tier(price=-100)])),
    ("pro_show price 0", "events", lambda: _event(ticketTiers=[f.tier(price=0)])),
    ("workshop with ticketTiers", "events", lambda: _workshop(ticketTiers=[f.tier()])),
    ("pro_show without artist", "events", lambda: _without(_event(), "artist")),
    ("cultural with an artist", "events", lambda: _event(type_="cultural", artist="Someone")),
    ("endTime before startTime", "events",
     lambda: _event(start=f.utc(2030, 1, 1, 12), hours=-1)),
    ("tier sold > capacity", "events", lambda: _event(ticketTiers=[f.tier(capacity=10, sold=11)])),
    ("workshop seatsTaken > seatLimit", "events", lambda: _workshop(seatLimit=5, seatsTaken=6)),
    ("duplicate tierIds in one event", "events", lambda: _event(ticketTiers=[f.tier("GOLD"), f.tier("GOLD")])),
    ("price stored as a double", "events", lambda: _event(ticketTiers=[f.tier(price=500.0)])),  # type: ignore[arg-type]
    ("unknown extra field", "events", lambda: _event(colour="red")),
    ("7 tickets in one booking", "bookings",
     lambda: _booking(tickets=[f.ticket() for _ in range(7)], totalAmount=7 * 500)),
    ("bad ticket code pattern", "bookings",
     lambda: _booking(tickets=[f.ticket(code="SPX-abc")], totalAmount=500)),
    ("totalAmount != sum of prices", "bookings", lambda: _booking(totalAmount=999)),
    ("workshop booking with 2 tickets", "bookings",
     lambda: _booking(eventType="workshop", tickets=[f.ticket(tier_id=None, price=0) for _ in range(2)],
                      totalAmount=0, paymentMode="FREE")),
    ("one booking spanning two tiers", "bookings",
     lambda: _booking(tickets=[f.ticket(tier_id="GOLD"), f.ticket(tier_id="SILVER")])),
    ("same code twice inside one booking", "bookings",
     lambda: _booking(tickets=[f.ticket(code="SPX-DUPE0001"), f.ticket(code="SPX-DUPE0001")])),
    ("confirmed booking with cancelledAt", "bookings", lambda: _booking(cancelledAt=f.utc(2026, 1, 1))),
    ("non-volunteer with shifts", "users",
     lambda: f.user_doc(role="attendee", shifts=[{"eventId": ObjectId(), "duty": "gate",
                                                   "start": f.utc(2030, 1, 1, 10), "end": f.utc(2030, 1, 1, 12)}])),
    ("shift ending before it starts", "users",
     lambda: f.user_doc(role="volunteer", shifts=[{"eventId": ObjectId(), "duty": "gate",
                                                    "start": f.utc(2030, 1, 1, 12), "end": f.utc(2030, 1, 1, 10)}])),
    ("uppercase email", "users", lambda: f.user_doc(email="Someone@Test.example")),
    ("phone not starting with 6-9", "users", lambda: f.user_doc(phone="5123456789")),
    ("venue without sections", "venues", lambda: f.venue_doc(sections=[])),
    ("venue with duplicate section names", "venues",
     lambda: f.venue_doc(sections=[{"name": "A", "capacity": 1}, {"name": "A", "capacity": 2}])),
]


@pytest.mark.parametrize("case,collection,build", REJECTED, ids=[c for c, _, _ in REJECTED])
def test_validator_rejects(db: Database, case: str, collection: str, build: Callable[[], dict[str, Any]]) -> None:
    with pytest.raises(WriteError) as exc_info:
        db[collection].insert_one(build())
    assert exc_info.value.code == DOCUMENT_VALIDATION_FAILURE
    assert db[collection].count_documents({}) == 0


@pytest.mark.parametrize("collection,build", [
    ("venues", f.venue_doc),
    ("events", _event),
    ("events", lambda: _event(type_="cultural", ticketTiers=[f.tier(price=0)])),
    ("events", _workshop),
    ("users", f.user_doc),
    ("bookings", _booking),
], ids=["venue", "pro_show", "free cultural", "workshop", "user", "booking"])
def test_valid_documents_are_accepted(db: Database, collection: str, build: Callable[[], dict[str, Any]]) -> None:
    """The validators are not simply rejecting everything."""
    db[collection].insert_one(build())
    assert db[collection].count_documents({}) == 1


def test_updates_are_validated_too(db: Database) -> None:
    """validationLevel strict: an $inc that would oversell is refused, not just inserts."""
    event_id = db.events.insert_one(_event(ticketTiers=[f.tier(capacity=10, sold=9)])).inserted_id
    with pytest.raises(WriteError) as exc_info:
        db.events.update_one({"_id": event_id, "ticketTiers.tierId": "GOLD"}, {"$inc": {"ticketTiers.$.sold": 2}})
    assert exc_info.value.code == DOCUMENT_VALIDATION_FAILURE
    assert db.events.find_one({"_id": event_id})["ticketTiers"][0]["sold"] == 9


def test_err_info_is_translated_into_readable_problems(db: Database) -> None:
    with pytest.raises(WriteError) as exc_info:
        db.bookings.insert_one(_booking(tickets=[f.ticket(code="SPX-abc")], totalAmount=999))
    problems = ValidationFailed.from_write_error(exc_info.value, "bookings").problems
    assert ("tickets[0].code", "regular expression did not match (got 'SPX-abc')") in problems
    assert ("document", "totalAmount must equal the sum of ticket prices") in problems


def test_err_info_for_oneof_shows_only_the_documents_own_type(db: Database) -> None:
    with pytest.raises(WriteError) as exc_info:
        db.events.insert_one(_without(_event(), "artist"))
    problems = describe_err_info("events", exc_info.value.details["errInfo"])
    assert problems == [("document", "missing artist")]


def test_valid_past_event_can_still_be_updated(db: Database) -> None:
    """No "startTime in the future" rule in the validator — otherwise completed events could not be updated."""
    event_id = db.events.insert_one(_event(start=f.utc(2020, 1, 1, 10))).inserted_id
    db.events.update_one({"_id": event_id}, {"$set": {"status": "completed"}})
    assert db.events.find_one({"_id": event_id})["status"] == "completed"
    assert timedelta(hours=3) == db.events.find_one({"_id": event_id})["endTime"] - f.utc(2020, 1, 1, 10)
