"""Service-level rules — the ones a validator cannot express because they span documents."""
from datetime import timedelta

import pytest
from bson import ObjectId
from pymongo.database import Database

from core import clock
from core.exceptions import NotFoundError, RuleViolation, ValidationFailed
from services import auth_service, booking_service, event_service, volunteer_service
from services.event_service import EventInput, TierInput, VenueInput
from tests import factories as f


@pytest.fixture
def venue_id(db: Database) -> ObjectId:
    return f.insert(db, "venues", f.venue_doc(name="Open Air", sections=[{"name": "Arena", "capacity": 300},
                                                                         {"name": "Steps", "capacity": 200}]))


def cultural_input(venue_id: ObjectId, days: int = 10, **changes: object) -> EventInput:
    start = clock.now() + timedelta(days=days)
    data = EventInput("Garba Night", "Dandiya and garba with live dhol.", "cultural", venue_id, start,
                      start + timedelta(hours=3), ["dance"],
                      tiers=[TierInput("GA", "General", "Arena", 99, 200), TierInput("FREE", "Steps", "Steps", 0, 100)])
    return EventInput(**{**data.__dict__, **changes})


# --- Events --------------------------------------------------------------------
def test_create_event_and_unique_slug(venue_id: ObjectId) -> None:
    first = event_service.create_event(cultural_input(venue_id))
    second = event_service.create_event(cultural_input(venue_id, days=20))
    assert (first["slug"], second["slug"]) == ("garba-night", "garba-night-2")


def test_tier_cannot_exceed_its_venue_section(venue_id: ObjectId) -> None:
    with pytest.raises(RuleViolation, match="exceeds section"):
        event_service.create_event(cultural_input(venue_id, tiers=[TierInput("GA", "General", "Arena", 99, 301)]))
    with pytest.raises(RuleViolation, match="no section"):
        event_service.create_event(cultural_input(venue_id, tiers=[TierInput("GA", "General", "Balcony", 99, 10)]))


def test_no_overlapping_events_in_a_venue(venue_id: ObjectId) -> None:
    created = event_service.create_event(cultural_input(venue_id))
    clash = cultural_input(venue_id, start=created["startTime"] + timedelta(hours=1),
                           end=created["endTime"] + timedelta(hours=1))
    with pytest.raises(RuleViolation, match="already scheduled"):
        event_service.create_event(clash)
    back_to_back = cultural_input(venue_id, start=created["endTime"], end=created["endTime"] + timedelta(hours=1))
    assert event_service.create_event(back_to_back)  # touching is not overlapping


def test_cancelled_events_do_not_block_the_slot(venue_id: ObjectId) -> None:
    created = event_service.create_event(cultural_input(venue_id))
    event_service.cancel_event(created["_id"])
    assert event_service.create_event(cultural_input(venue_id, start=created["startTime"], end=created["endTime"]))


def test_shape_errors_come_from_the_validator(venue_id: ObjectId) -> None:
    with pytest.raises(ValidationFailed) as exc_info:
        event_service.create_event(cultural_input(venue_id, type="pro_show"))  # no artist
    assert ("document", "missing artist") in exc_info.value.problems


def test_edit_tiers_atomically(db: Database, venue_id: ObjectId) -> None:
    event = event_service.create_event(cultural_input(venue_id))
    user = f.insert(db, "users", f.user_doc())
    booking_service.book(user, event["_id"], "GA", 4, "UPI")
    edited = cultural_input(venue_id, title="Garba Night 2026", start=event["startTime"], end=event["endTime"],
                            tiers=[TierInput("GA", "General", "Arena", 149, 150), TierInput("VIP", "VIP", "Arena", 299, 20)])
    updated = event_service.update_event(event["_id"], edited)
    assert [(t["tierId"], t["price"], t["capacity"], t["sold"]) for t in updated["ticketTiers"]] == [
        ("GA", 149, 150, 4), ("VIP", 299, 20, 0)]
    with pytest.raises(RuleViolation, match="below"):
        event_service.update_event(event["_id"], EventInput(**{**edited.__dict__, "tiers": [
            TierInput("GA", "General", "Arena", 149, 3), TierInput("VIP", "VIP", "Arena", 299, 20)]}))
    with pytest.raises(RuleViolation, match="cannot remove"):
        event_service.update_event(event["_id"], EventInput(**{**edited.__dict__, "tiers": [
            TierInput("VIP", "VIP", "Arena", 299, 20)]}))
    with pytest.raises(RuleViolation, match="type cannot"):
        event_service.update_event(event["_id"], EventInput(**{**edited.__dict__, "type": "pro_show"}))


def test_cancel_event_refunds_everyone_and_frees_seats(db: Database, venue_id: ObjectId) -> None:
    event = event_service.create_event(cultural_input(venue_id))
    users = [f.insert(db, "users", f.user_doc()) for _ in range(3)]
    booking_service.book(users[0], event["_id"], "GA", 2, "UPI")
    booking_service.book(users[1], event["_id"], "GA", 1, "CARD")
    booking_service.book(users[2], event["_id"], "FREE", 2, "")
    assert event_service.cancel_event(event["_id"]) == 3
    statuses = sorted(b["status"] for b in db.bookings.find({"eventId": event["_id"]}))
    assert statuses == ["cancelled", "refunded", "refunded"]  # the free booking is "cancelled"
    assert all(t["sold"] == 0 for t in db.events.find_one({"_id": event["_id"]})["ticketTiers"])
    with pytest.raises(RuleViolation):
        booking_service.book(users[0], event["_id"], "GA", 1, "UPI")


def test_hard_delete_only_without_bookings(db: Database, venue_id: ObjectId) -> None:
    event = event_service.create_event(cultural_input(venue_id))
    booking_service.book(f.insert(db, "users", f.user_doc()), event["_id"], "GA", 1, "UPI")
    with pytest.raises(RuleViolation, match="cancel it instead"):
        event_service.delete_event(event["_id"])
    empty = event_service.create_event(cultural_input(venue_id, days=30))
    event_service.delete_event(empty["_id"])
    assert db.events.count_documents({"_id": empty["_id"]}) == 0


# --- Venues --------------------------------------------------------------------
def test_venue_in_use_cannot_be_deleted_or_shrunk(venue_id: ObjectId) -> None:
    event_service.create_event(cultural_input(venue_id))
    with pytest.raises(RuleViolation, match="cannot be deleted"):
        event_service.delete_venue(venue_id)
    with pytest.raises(RuleViolation, match="exceeds section"):
        event_service.update_venue(venue_id, VenueInput("Open Air", "B", [("Arena", 50), ("Steps", 200)]))


def test_import_venues_is_idempotent(db: Database) -> None:
    rows = [VenueInput("Hall X", "B1", [("Main", 100)], ["ac"]), VenueInput("Hall Y", "B2", [("Main", 50)])]
    assert event_service.import_venues(rows) == (2, 0)
    assert event_service.import_venues(rows) == (0, 2)
    assert db.venues.count_documents({}) == 2


# --- Volunteers ----------------------------------------------------------------
def test_shift_rules(db: Database, venue_id: ObjectId) -> None:
    event = event_service.create_event(cultural_input(venue_id))
    volunteer = f.insert(db, "users", f.user_doc("volunteer"))
    attendee = f.insert(db, "users", f.user_doc())
    shift = volunteer_service.assign_shift(volunteer, event["_id"], "gate")
    assert (shift["start"], shift["end"]) == (event["startTime"], event["endTime"])
    with pytest.raises(RuleViolation, match="overlapping"):
        volunteer_service.assign_shift(volunteer, event["_id"], "stage")
    with pytest.raises(RuleViolation, match="inside"):
        volunteer_service.assign_shift(volunteer, event["_id"], "gate",
                                       event["startTime"] - timedelta(hours=1), event["startTime"])
    with pytest.raises(NotFoundError):
        volunteer_service.assign_shift(attendee, event["_id"], "gate")
    volunteer_service.remove_shift(volunteer, event["_id"], shift["start"])
    assert db.users.find_one({"_id": volunteer})["shifts"] == []


# --- Accounts ------------------------------------------------------------------
def test_register_and_authenticate(db: Database) -> None:
    user = auth_service.register("Riya Verma", "Riya.V@Example.com", "9812345678", "pw-123456", "LPU", "12345678")
    assert user["email"] == "riya.v@example.com"
    assert "passwordHash" not in user
    assert auth_service.authenticate("RIYA.V@example.com", "pw-123456")["_id"] == user["_id"]
    assert auth_service.authenticate("riya.v@example.com", "wrong") is None
    assert auth_service.authenticate("nobody@example.com", "pw-123456") is None


def test_cleanup_only_removes_old_attendees_without_bookings(db: Database, venue_id: ObjectId) -> None:
    old = clock.now() - timedelta(days=90)
    idle = f.insert(db, "users", f.user_doc(createdAt=old))
    active = f.insert(db, "users", f.user_doc(createdAt=old))
    fresh = f.insert(db, "users", f.user_doc(createdAt=clock.now()))
    volunteer = f.insert(db, "users", f.user_doc("volunteer", createdAt=old))
    event = event_service.create_event(cultural_input(venue_id))
    booking_service.book(active, event["_id"], "GA", 1, "UPI")
    assert auth_service.cleanup_inactive_attendees(30) == 1
    remaining = {u["_id"] for u in db.users.find()}
    assert idle not in remaining and {active, fresh, volunteer} <= remaining
