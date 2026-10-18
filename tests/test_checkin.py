"""Gate check-in: atomic, never double-counts, refuses cancelled bookings."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from bson import ObjectId
from pymongo.database import Database

from services import booking_service, checkin_service
from tests import factories as f


@pytest.fixture
def booking(db: Database) -> dict:
    venue_id = f.insert(db, "venues", f.venue_doc())
    event_id = f.insert(db, "events", f.event_doc(venue_id))
    user_id = f.insert(db, "users", f.user_doc(name="Asha Rao"))
    return booking_service.book(user_id, event_id, "GOLD", 2, "UPI")


def checked_in(db: Database, booking_id: ObjectId) -> int:
    return sum(1 for t in db.bookings.find_one({"_id": booking_id})["tickets"] if t["checkedInAt"])


def test_check_in_ok(db: Database, booking: dict) -> None:
    result = checkin_service.check_in(booking["tickets"][0]["code"])
    assert result.outcome == "ok"
    assert result.colour == "success"
    assert result.holder == "Asha Rao"
    assert checked_in(db, booking["_id"]) == 1


def test_double_scan_does_not_double_count(db: Database, booking: dict) -> None:
    code = booking["tickets"][0]["code"]
    first = checkin_service.check_in(code)
    second = checkin_service.check_in(code.lower())  # scanners/keyboards vary in case
    assert (first.outcome, second.outcome) == ("ok", "already")
    assert second.checked_in_at == first.checked_in_at
    assert checked_in(db, booking["_id"]) == 1


def test_simultaneous_scans_at_two_gates_count_once(db: Database, booking: dict) -> None:
    code = booking["tickets"][1]["code"]
    with ThreadPoolExecutor(max_workers=20) as pool:
        outcomes = list(pool.map(lambda _: checkin_service.check_in(code).outcome, range(20)))
    assert outcomes.count("ok") == 1
    assert outcomes.count("already") == 19
    assert checked_in(db, booking["_id"]) == 1


def test_cancelled_booking_cannot_check_in(db: Database, booking: dict) -> None:
    booking_service.cancel(booking["userId"], booking["_id"])
    result = checkin_service.check_in(booking["tickets"][0]["code"])
    assert result.outcome == "cancelled"
    assert result.colour == "danger"
    assert checked_in(db, booking["_id"]) == 0


@pytest.mark.parametrize("code", ["SPX-ZZZZZZZZ", "hello", "", "SPX-123"])
def test_unknown_or_malformed_codes_are_invalid(booking: dict, code: str) -> None:
    assert checkin_service.check_in(code).outcome == "invalid"
