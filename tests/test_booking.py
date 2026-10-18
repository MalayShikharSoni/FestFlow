"""Booking, overselling, compensation and cancellation (booking_service)."""
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from bson import ObjectId
from pymongo.database import Database

from core.exceptions import NotFoundError, RuleViolation, SoldOutError
from repositories import bookings_repo
from services import booking_service, checkin_service, event_service
from tests import factories as f


@pytest.fixture
def venue_id(db: Database) -> ObjectId:
    return f.insert(db, "venues", f.venue_doc())


@pytest.fixture
def user_id(db: Database) -> ObjectId:
    return f.insert(db, "users", f.user_doc())


def make_event(db: Database, venue_id: ObjectId, **overrides: Any) -> ObjectId:
    return f.insert(db, "events", f.event_doc(venue_id, **overrides))


def tier_sold(db: Database, event_id: ObjectId, tier_id: str = "GOLD") -> int:
    event = db.events.find_one({"_id": event_id})
    return next(t["sold"] for t in event["ticketTiers"] if t["tierId"] == tier_id)


# --- Booking -------------------------------------------------------------------
def test_booking_claims_seats_and_stores_tickets(db: Database, venue_id: ObjectId, user_id: ObjectId) -> None:
    event_id = make_event(db, venue_id, ticketTiers=[f.tier(price=700, capacity=10)])
    booking = booking_service.book(user_id, event_id, "GOLD", 3, "UPI")
    assert booking["status"] == "confirmed"
    assert booking["totalAmount"] == 2100
    assert booking["eventType"] == "pro_show"
    assert len({t["code"] for t in booking["tickets"]}) == 3
    assert tier_sold(db, event_id) == 3


def test_free_tier_is_always_payment_mode_free(db: Database, venue_id: ObjectId, user_id: ObjectId) -> None:
    event_id = make_event(db, venue_id, type_="cultural", ticketTiers=[f.tier("FREE", price=0)])
    booking = booking_service.book(user_id, event_id, "FREE", 2, "CARD")
    assert (booking["paymentMode"], booking["totalAmount"]) == ("FREE", 0)


def test_not_enough_seats_raises_sold_out(db: Database, venue_id: ObjectId, user_id: ObjectId) -> None:
    event_id = make_event(db, venue_id, ticketTiers=[f.tier(capacity=10, sold=8)])
    with pytest.raises(SoldOutError, match="Only 2"):
        booking_service.book(user_id, event_id, "GOLD", 3, "UPI")
    assert tier_sold(db, event_id) == 8
    assert db.bookings.count_documents({}) == 0


@pytest.mark.parametrize("qty", [0, 7])
def test_quantity_must_be_1_to_6(db: Database, venue_id: ObjectId, user_id: ObjectId, qty: int) -> None:
    event_id = make_event(db, venue_id)
    with pytest.raises(RuleViolation):
        booking_service.book(user_id, event_id, "GOLD", qty, "UPI")


def test_cannot_book_started_or_closed_events(db: Database, venue_id: ObjectId, user_id: ObjectId) -> None:
    past = make_event(db, venue_id, start=f.utc(2020, 1, 1, 10), status="completed")
    cancelled = make_event(db, venue_id, status="cancelled")
    for event_id in (past, cancelled):
        with pytest.raises(RuleViolation):
            booking_service.book(user_id, event_id, "GOLD", 1, "UPI")


def test_unknown_event_or_tier(db: Database, venue_id: ObjectId, user_id: ObjectId) -> None:
    with pytest.raises(NotFoundError):
        booking_service.book(user_id, ObjectId(), "GOLD", 1, "UPI")
    with pytest.raises(NotFoundError):
        booking_service.book(user_id, make_event(db, venue_id), "PLATINUM", 1, "UPI")


def test_overselling_race_50_threads_capacity_10(db: Database, venue_id: ObjectId) -> None:
    """The headline test: 50 people try to buy the last 10 seats at the same moment."""
    event_id = make_event(db, venue_id, ticketTiers=[f.tier(capacity=10)])
    buyers = [f.insert(db, "users", f.user_doc()) for _ in range(50)]

    def attempt(user: ObjectId) -> str:
        try:
            booking_service.book(user, event_id, "GOLD", 1, "UPI")
            return "ok"
        except SoldOutError:
            return "sold out"

    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(attempt, buyers))

    assert results.count("ok") == 10
    assert results.count("sold out") == 40
    assert tier_sold(db, event_id) == 10
    assert db.bookings.count_documents({"eventId": event_id}) == 10


def test_workshop_race_never_exceeds_seat_limit(db: Database, venue_id: ObjectId) -> None:
    event_id = make_event(db, venue_id, type_="workshop", seatLimit=5)
    people = [f.insert(db, "users", f.user_doc()) for _ in range(20)]

    def attempt(user: ObjectId) -> bool:
        try:
            booking_service.book(user, event_id, None, 1, "")
            return True
        except SoldOutError:
            return False

    with ThreadPoolExecutor(max_workers=20) as pool:
        assert sum(pool.map(attempt, people)) == 5
    assert db.events.find_one({"_id": event_id})["seatsTaken"] == 5


def test_workshop_one_seat_per_person(db: Database, venue_id: ObjectId, user_id: ObjectId) -> None:
    event_id = make_event(db, venue_id, type_="workshop")
    booking_service.book(user_id, event_id, None, 1, "")
    with pytest.raises(RuleViolation, match="already registered"):
        booking_service.book(user_id, event_id, None, 1, "")


def test_failed_insert_gives_the_seats_back(db: Database, venue_id: ObjectId, user_id: ObjectId,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Compensation: the seat claim is undone if the booking cannot be written."""
    event_id = make_event(db, venue_id, ticketTiers=[f.tier(capacity=10, sold=4)])

    def broken_insert(doc: dict[str, Any]) -> ObjectId:
        raise ConnectionError("simulated failure after the seats were claimed")

    monkeypatch.setattr(bookings_repo, "insert_booking", broken_insert)
    with pytest.raises(ConnectionError):
        booking_service.book(user_id, event_id, "GOLD", 3, "UPI")
    assert tier_sold(db, event_id) == 4
    assert db.bookings.count_documents({}) == 0


def test_event_cancelled_mid_booking_refunds_the_booking(db: Database, venue_id: ObjectId, user_id: ObjectId,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Race: the organizer cancels between our seat claim and our insert."""
    event_id = make_event(db, venue_id, ticketTiers=[f.tier(capacity=10)])
    real_insert = bookings_repo.insert_booking

    def insert_after_cancel(doc: dict[str, Any]) -> ObjectId:
        event_service.cancel_event(event_id)  # happens "in between"
        return real_insert(doc)

    monkeypatch.setattr(bookings_repo, "insert_booking", insert_after_cancel)
    with pytest.raises(RuleViolation, match="cancelled while you were booking"):
        booking_service.book(user_id, event_id, "GOLD", 2, "UPI")
    assert db.bookings.find_one({"eventId": event_id})["status"] == "refunded"
    assert tier_sold(db, event_id) == 0


# --- Cancellation --------------------------------------------------------------
def test_cancellation_releases_seats_and_refunds(db: Database, venue_id: ObjectId, user_id: ObjectId) -> None:
    event_id = make_event(db, venue_id, ticketTiers=[f.tier(capacity=10)])
    booking = booking_service.book(user_id, event_id, "GOLD", 4, "UPI")
    cancelled = booking_service.cancel(user_id, booking["_id"])
    assert cancelled["status"] == "refunded"
    assert cancelled["cancelledAt"] is not None
    assert tier_sold(db, event_id) == 0


def test_free_booking_cancels_as_cancelled_not_refunded(db: Database, venue_id: ObjectId, user_id: ObjectId) -> None:
    event_id = make_event(db, venue_id, type_="workshop")
    booking = booking_service.book(user_id, event_id, None, 1, "")
    assert booking_service.cancel(user_id, booking["_id"])["status"] == "cancelled"
    assert db.events.find_one({"_id": event_id})["seatsTaken"] == 0


def test_cannot_cancel_after_check_in(db: Database, venue_id: ObjectId, user_id: ObjectId) -> None:
    event_id = make_event(db, venue_id)
    booking = booking_service.book(user_id, event_id, "GOLD", 2, "UPI")
    assert checkin_service.check_in(booking["tickets"][0]["code"]).outcome == "ok"
    with pytest.raises(RuleViolation, match="scanned"):
        booking_service.cancel(user_id, booking["_id"])
    assert tier_sold(db, event_id) == 2


def test_cannot_cancel_twice_or_someone_elses(db: Database, venue_id: ObjectId, user_id: ObjectId) -> None:
    event_id = make_event(db, venue_id)
    booking = booking_service.book(user_id, event_id, "GOLD", 1, "UPI")
    other = f.insert(db, "users", f.user_doc())
    with pytest.raises(NotFoundError):
        booking_service.cancel(other, booking["_id"])
    booking_service.cancel(user_id, booking["_id"])
    with pytest.raises(RuleViolation, match="already"):
        booking_service.cancel(user_id, booking["_id"])
    assert tier_sold(db, event_id) == 0  # released exactly once


def test_cannot_cancel_after_event_started(db: Database, venue_id: ObjectId, user_id: ObjectId) -> None:
    event_id = make_event(db, venue_id, start=f.utc(2020, 1, 1, 10))
    booking_id = f.insert(db, "bookings", f.booking_doc(user_id, event_id, [f.ticket()]))
    with pytest.raises(RuleViolation, match="started"):
        booking_service.cancel(user_id, booking_id)
