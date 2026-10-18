"""
Small builders for valid documents. Each returns a dict that passes the
validators as-is; tests override fields with keyword arguments.
Inserted straight with PyMongo: tests set up state directly instead of going
through services, so a service bug cannot hide inside a fixture.
"""
from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Any

from bson import ObjectId
from django.contrib.auth.hashers import make_password
from pymongo.database import Database

from core import clock

PASSWORD = "Spectra@2026"
_HASH: list[str] = []  # computed once: PBKDF2 is deliberately slow
_seq = count(1)


def password_hash() -> str:
    if not _HASH:
        _HASH.append(make_password(PASSWORD))
    return _HASH[0]


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def venue_doc(**overrides: Any) -> dict[str, Any]:
    n = next(_seq)
    doc = {
        "name": f"Test Venue {n}",
        "block": "Block 1",
        "sections": [{"name": "A", "capacity": 100}, {"name": "B", "capacity": 50}],
        "facilities": ["stage"],
        "createdAt": clock.now(),
    }
    return {**doc, **overrides}


def tier(tier_id: str = "GOLD", price: int = 500, capacity: int = 10, sold: int = 0,
         section: str = "A", name: str | None = None) -> dict[str, Any]:
    return {"tierId": tier_id, "name": name or tier_id.title(), "section": section,
            "price": price, "capacity": capacity, "sold": sold}


def event_doc(venue_id: ObjectId, type_: str = "pro_show", start: datetime | None = None,
              hours: float = 3, **overrides: Any) -> dict[str, Any]:
    n = next(_seq)
    start = start or clock.now() + timedelta(days=10 + n)
    doc: dict[str, Any] = {
        "title": f"Test Event {n}",
        "slug": f"test-event-{n}",
        "description": "An event created by the test suite.",
        "type": type_,
        "venueId": venue_id,
        "startTime": start,
        "endTime": start + timedelta(hours=hours),
        "status": "scheduled",
        "tags": [],
        "createdAt": clock.now(),
        "updatedAt": clock.now(),
    }
    if type_ == "workshop":
        doc.update(seatLimit=20, seatsTaken=0, instructor="Dr. Test", prerequisites=[])
    else:
        doc["ticketTiers"] = [tier()]
        if type_ == "pro_show":
            doc["artist"] = "Test Artist"
    return {**doc, **overrides}


def user_doc(role: str = "attendee", **overrides: Any) -> dict[str, Any]:
    n = next(_seq)
    doc = {
        "name": f"Test User {n}",
        "email": f"user{n}@test.example",
        "phone": "9876543210",
        "role": role,
        "passwordHash": password_hash(),
        "college": "Lovely Professional University",
        "shifts": [],
        "createdAt": clock.now() - timedelta(days=30),
    }
    return {**doc, **overrides}


def ticket(code: str | None = None, tier_id: str | None = "GOLD", price: int = 500,
           checked_in_at: datetime | None = None) -> dict[str, Any]:
    return {"code": code or f"SPX-T{next(_seq):07d}", "tierId": tier_id, "price": price,
            "checkedInAt": checked_in_at}


def booking_doc(user_id: ObjectId, event_id: ObjectId, tickets: list[dict[str, Any]],
                event_type: str = "pro_show", status: str = "confirmed", payment_mode: str = "UPI",
                booked_at: datetime | None = None, cancelled_at: datetime | None = None) -> dict[str, Any]:
    total = sum(t["price"] for t in tickets)
    return {
        "userId": user_id,
        "eventId": event_id,
        "eventType": event_type,
        "status": status,
        "paymentMode": "FREE" if total == 0 else payment_mode,
        "tickets": tickets,
        "totalAmount": total,
        "bookedAt": booked_at or clock.now() - timedelta(days=1),
        "cancelledAt": cancelled_at,
    }


def insert(db: Database, collection: str, doc: dict[str, Any]) -> ObjectId:
    return db[collection].insert_one(doc).inserted_id
