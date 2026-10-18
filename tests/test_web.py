"""End-to-end web flows with Django's test client (no browser)."""
from datetime import timedelta

import pytest
from bson import ObjectId
from django.test import Client
from pymongo.database import Database

from core import clock
from tests import factories as f


@pytest.fixture
def world(db: Database) -> dict[str, ObjectId]:
    venue_id = f.insert(db, "venues", f.venue_doc(name="Central Lawns"))
    return {
        "venue": venue_id,
        "event": f.insert(db, "events", f.event_doc(venue_id, title="Rock Night", slug="rock-night",
                                                    ticketTiers=[f.tier("GOLD", 999, 10), f.tier("FANPIT", 1999, 5, 5)])),
        "attendee": f.insert(db, "users", f.user_doc(email="fan@test.example", name="Fan One")),
        "organizer": f.insert(db, "users", f.user_doc("organizer", email="boss@test.example")),
    }


def logged_in(email: str) -> Client:
    client = Client()
    response = client.post("/login/", {"email": email, "password": f.PASSWORD})
    assert response.status_code == 302
    return client


def test_public_pages(world: dict) -> None:
    client = Client()
    assert "Rock Night" in client.get("/").content.decode()
    assert "Rock Night" in client.get("/?q=rock").content.decode()
    assert "Rock Night" not in client.get("/?type=workshop").content.decode()
    assert client.get(f"/events/{world['event']}/").status_code == 200
    assert client.get("/events/not-an-id/").status_code == 404
    assert client.get(f"/events/{ObjectId()}/").status_code == 404


def test_login_required_and_roles(world: dict) -> None:
    response = Client().get("/my/bookings/")
    assert response.status_code == 302 and response["Location"] == "/login/?next=/my/bookings/"
    assert logged_in("fan@test.example").get("/organizer/").status_code == 403
    assert logged_in("boss@test.example").get("/organizer/").status_code == 200


def test_login_ignores_offsite_next(world: dict) -> None:
    response = Client().post("/login/", {"email": "fan@test.example", "password": f.PASSWORD,
                                         "next": "https://evil.example/"})
    assert response["Location"] == "/"


def test_book_view_ticket_and_cancel(db: Database, world: dict) -> None:
    client = logged_in("fan@test.example")
    response = client.post(f"/events/{world['event']}/book/", {"tier": "GOLD", "qty": 2, "payment_mode": "UPI"})
    assert response.status_code == 302
    ticket_page = client.get(response["Location"]).content.decode()
    assert ticket_page.count("data:image/png;base64,") == 2  # one QR per ticket
    client.post(response["Location"] + "cancel/")
    assert db.bookings.find_one({})["status"] == "refunded"
    assert db.events.find_one({"_id": world["event"]})["ticketTiers"][0]["sold"] == 0


def test_sold_out_tier_shows_message(world: dict) -> None:
    page = logged_in("fan@test.example").post(f"/events/{world['event']}/book/",
                                               {"tier": "FANPIT", "qty": 1, "payment_mode": "UPI"}).content.decode()
    assert "sold out" in page


def test_workshop_registration_with_an_empty_post(db: Database, world: dict) -> None:
    """Regression: an empty POST QueryDict is falsy; the form must still be bound."""
    workshop = f.insert(db, "events", f.event_doc(world["venue"], "workshop"))
    response = logged_in("fan@test.example").post(f"/events/{workshop}/book/", {})
    assert response.status_code == 302
    assert db.events.find_one({"_id": workshop})["seatsTaken"] == 1


def test_validator_error_lands_on_the_form_field(db: Database) -> None:
    page = Client().post("/register/", {
        "name": "Neha", "email": "neha@test.example", "phone": "12345", "college": "LPU", "reg_no": "",
        "password": "longpassword", "password2": "longpassword"}).content.decode()
    assert "regular expression did not match" in page
    assert db.users.count_documents({}) == 0


def test_organizer_creates_event_through_the_form(db: Database, world: dict) -> None:
    start = (clock.now() + timedelta(days=40)).astimezone().strftime("%Y-%m-%dT%H:%M")
    end = (clock.now() + timedelta(days=40, hours=2)).astimezone().strftime("%Y-%m-%dT%H:%M")
    response = logged_in("boss@test.example").post("/organizer/events/new/", {
        "title": "Jazz Evening", "description": "Smooth jazz on the lawns.", "type": "cultural",
        "venue": str(world["venue"]), "start": start, "end": end, "tags": "music", "artist": "",
        "instructor": "", "prerequisites": "", "seat_limit": "",
        "tiers-TOTAL_FORMS": "1", "tiers-INITIAL_FORMS": "0", "tiers-MIN_NUM_FORMS": "0", "tiers-MAX_NUM_FORMS": "1000",
        "tiers-0-tier_id": "ga", "tiers-0-name": "General", "tiers-0-section": "A",
        "tiers-0-price": "199", "tiers-0-capacity": "80"})
    assert response.status_code == 302
    event = db.events.find_one({"slug": "jazz-evening"})
    assert event["ticketTiers"][0]["tierId"] == "GA"


def test_checkin_screen_and_query_lab(db: Database, world: dict) -> None:
    client = logged_in("boss@test.example")
    db.bookings.insert_one(f.booking_doc(world["attendee"], world["event"], [f.ticket(code="SPX-GATE0001")]))
    assert "Admit" in client.post("/organizer/checkin/", {"code": "spx-gate0001"}).content.decode()
    assert "Already used" in client.post("/organizer/checkin/", {"code": "SPX-GATE0001"}).content.decode()
    page = client.post("/organizer/query-lab/", {"collection": "bookings", "pipeline": '[{"$out": "x"}]'}).content.decode()
    assert "read-only" in page
