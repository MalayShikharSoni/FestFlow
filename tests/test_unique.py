"""Unique indexes: MongoDB answers code 11000; services surface it as DuplicateError."""
import pytest
from bson import ObjectId
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from core.exceptions import DuplicateError
from services import auth_service, event_service
from services.event_service import VenueInput
from tests import factories as f

DUPLICATE_KEY = 11000


def test_duplicate_email_raw(db: Database) -> None:
    db.users.insert_one(f.user_doc(email="same@test.example"))
    with pytest.raises(DuplicateKeyError) as exc_info:
        db.users.insert_one(f.user_doc(email="same@test.example"))
    assert exc_info.value.code == DUPLICATE_KEY


def test_duplicate_email_via_registration_is_case_insensitive(db: Database) -> None:
    auth_service.register("Asha", "asha@test.example", "9876543210", "password123", "LPU")
    with pytest.raises(DuplicateError) as exc_info:
        auth_service.register("Asha 2", "ASHA@Test.Example", "9876543210", "password123", "LPU")
    assert exc_info.value.fields == ["email"]
    assert db.users.count_documents({}) == 1


def test_duplicate_ticket_code_across_bookings(db: Database) -> None:
    db.bookings.insert_one(f.booking_doc(ObjectId(), ObjectId(), [f.ticket(code="SPX-SAME0001")]))
    with pytest.raises(DuplicateKeyError) as exc_info:
        db.bookings.insert_one(f.booking_doc(ObjectId(), ObjectId(), [f.ticket(), f.ticket(code="SPX-SAME0001")]))
    assert exc_info.value.code == DUPLICATE_KEY
    assert exc_info.value.details["keyValue"] == {"tickets.code": "SPX-SAME0001"}


def test_duplicate_slug_and_venue_name(db: Database) -> None:
    venue_id = db.venues.insert_one(f.venue_doc(name="Hall One")).inserted_id
    with pytest.raises(DuplicateKeyError):
        db.venues.insert_one(f.venue_doc(name="Hall One"))
    db.events.insert_one(f.event_doc(venue_id, slug="same-slug"))
    with pytest.raises(DuplicateKeyError):
        db.events.insert_one(f.event_doc(venue_id, slug="same-slug"))
    with pytest.raises(DuplicateError):
        event_service.create_venue(VenueInput("Hall One", "B", [("Main", 10)]))
