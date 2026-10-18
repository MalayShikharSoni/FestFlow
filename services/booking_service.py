"""
Booking and cancelling tickets — the business rules around the atomic seat claim.

Flow of book():
  1. Load the event; refuse if it is not scheduled or has already started.
  2. Claim seats with ONE atomic find_one_and_update on the event document
     (events_repo.claim_tier_seats / claim_workshop_seat). None -> SoldOutError.
  3. Insert the booking with freshly generated ticket codes.
  4. If that insert fails, COMPENSATE: give the seats back with $inc -qty.

Why compensation and not a multi-document transaction?
    The seat claim (event document) and the booking (bookings collection) are
    two documents, so making them all-or-nothing would need a transaction —
    and transactions need a replica set; our dev server is a single node. The
    step that must never go wrong — two buyers taking the same last seat — is a
    single-document update, which MongoDB always applies atomically. If the
    second step fails we undo the first. In production (replica set / Atlas) we
    would wrap steps 2-3 in a transaction instead; see the report.
"""
import secrets
import string
from typing import Any

from bson import ObjectId

from core import clock
from core.exceptions import DuplicateError, NotFoundError, RuleViolation, SoldOutError, ValidationFailed
from repositories import bookings_repo, events_repo, venues_repo

MAX_TICKETS_PER_BOOKING = 6
PAID_MODES = ("UPI", "CARD", "NETBANKING", "CASH")
CODE_ALPHABET = string.ascii_uppercase + string.digits
CODE_ATTEMPTS = 3


def new_ticket_code() -> str:
    """SPX- + 8 random characters (36^8 ~ 2.8 trillion possibilities).

    `secrets`, not `random`: codes are what gets you through the gate, so they
    must not be guessable. The unique index on tickets.code catches the
    (astronomically rare) collision; we then simply generate new codes.
    """
    return "SPX-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))


# --- Booking -----------------------------------------------------------------
def book(user_id: ObjectId, event_id: ObjectId, tier_id: str | None, qty: int,
         payment_mode: str) -> dict[str, Any]:
    now = clock.now()
    event = events_repo.get_event(event_id)
    if event is None:
        raise NotFoundError("Event not found.")
    _ensure_open(event, now)
    if event["type"] == "workshop":
        return _book_workshop(user_id, event, now)
    return _book_tier(user_id, event, tier_id, qty, payment_mode, now)


def _ensure_open(event: dict[str, Any], now: Any) -> None:
    if event["status"] != "scheduled":
        raise RuleViolation(f"Bookings are closed: this event is {event['status']}.")
    if event["startTime"] <= now:
        raise RuleViolation("This event has already started.")


def _book_tier(user_id: ObjectId, event: dict[str, Any], tier_id: str | None, qty: int,
               payment_mode: str, now: Any) -> dict[str, Any]:
    if not 1 <= qty <= MAX_TICKETS_PER_BOOKING:
        raise RuleViolation(f"You can book 1 to {MAX_TICKETS_PER_BOOKING} tickets at a time.")
    tier = next((t for t in event["ticketTiers"] if t["tierId"] == tier_id), None)
    if tier is None:
        raise NotFoundError("That ticket tier does not exist.")
    if tier["price"] == 0:
        payment_mode = "FREE"
    elif payment_mode not in PAID_MODES:
        raise RuleViolation("Choose a payment mode.")

    try:
        claimed = events_repo.claim_tier_seats(event["_id"], tier_id, qty, tier["capacity"], now)
    except ValidationFailed:
        # Capacity was lowered after we read it and the validator refused to let
        # sold exceed it — the database-level safety net doing its job.
        claimed = None
    if claimed is None:
        _raise_unavailable(event["_id"], tier_id, qty)

    tickets = _new_tickets(qty, tier_id, tier["price"])
    booking = _booking_doc(user_id, event, tickets, payment_mode, now)
    return _insert_or_compensate(
        booking, release=lambda: events_repo.release_tier_seats(event["_id"], tier_id, qty, clock.now()))


def _book_workshop(user_id: ObjectId, event: dict[str, Any], now: Any) -> dict[str, Any]:
    # Cross-document rule (a validator only ever sees the one booking being written,
    # so it cannot know about the user's other bookings): one seat per person.
    if bookings_repo.has_confirmed_booking(user_id, event["_id"]):
        raise RuleViolation("You are already registered for this workshop.")
    if events_repo.claim_workshop_seat(event["_id"], now) is None:
        _raise_unavailable(event["_id"], None, 1)
    tickets = _new_tickets(1, None, 0)  # workshop registration is free
    booking = _booking_doc(user_id, event, tickets, "FREE", now)
    return _insert_or_compensate(
        booking, release=lambda: events_repo.release_workshop_seat(event["_id"], clock.now()))


def _raise_unavailable(event_id: ObjectId, tier_id: str | None, qty: int) -> None:
    """The claim did not match. Re-read once to say *why* (closed vs not enough seats)."""
    event = events_repo.get_event(event_id)
    if event is None or event["status"] != "scheduled" or event["startTime"] <= clock.now():
        raise RuleViolation("Bookings for this event have just closed.")
    if tier_id is None:
        raise SoldOutError("Sorry, this workshop is full.")
    tier = next(t for t in event["ticketTiers"] if t["tierId"] == tier_id)
    left = tier["capacity"] - tier["sold"]
    if left <= 0:
        raise SoldOutError(f"Sorry, {tier['name']} is sold out.")
    raise SoldOutError(f"Only {left} {tier['name']} ticket(s) left — you asked for {qty}.")


def _new_tickets(qty: int, tier_id: str | None, price: int) -> list[dict[str, Any]]:
    return [{"code": new_ticket_code(), "tierId": tier_id, "price": price, "checkedInAt": None}
            for _ in range(qty)]


def _booking_doc(user_id: ObjectId, event: dict[str, Any], tickets: list[dict[str, Any]],
                 payment_mode: str, now: Any) -> dict[str, Any]:
    return {
        "userId": user_id,
        "eventId": event["_id"],
        "eventType": event["type"],  # denormalized: an event's type never changes
        "status": "confirmed",
        "paymentMode": payment_mode,
        "tickets": tickets,
        "totalAmount": sum(t["price"] for t in tickets),
        "bookedAt": now,
        "cancelledAt": None,
    }


def _insert_or_compensate(booking: dict[str, Any], release: Any) -> dict[str, Any]:
    """Insert the booking; if that fails for any reason, hand the claimed seats back."""
    for attempt in range(CODE_ATTEMPTS):
        try:
            booking_id = bookings_repo.insert_booking(booking)
            break
        except DuplicateError:
            # The only unique index on bookings is tickets.code: a code collision.
            if attempt == CODE_ATTEMPTS - 1:
                release()
                raise
            for ticket in booking["tickets"]:
                ticket["code"] = new_ticket_code()
        except Exception:
            release()  # compensation: undo the seat claim
            raise
    _refund_if_cancelled_meanwhile(booking["eventId"], booking_id)
    return bookings_repo.get_booking(booking_id)


def _refund_if_cancelled_meanwhile(event_id: ObjectId, booking_id: ObjectId) -> None:
    """Close a small race with event_service.cancel_event.

    If the organizer cancelled the event after our seat claim but before our
    insert, cancel_event's refund pass may have missed this booking. Its stock
    reset runs after the status change, so the seats are already zeroed; we only
    need to refund the booking itself.
    """
    event = events_repo.get_event(event_id)
    if event and event["status"] == "cancelled":
        bookings_repo.refund_all_for_event(event_id, clock.now(), booking_id=booking_id)
        raise RuleViolation("This event was cancelled while you were booking. Nothing was charged.")


# --- Cancelling --------------------------------------------------------------
def cancel(user_id: ObjectId, booking_id: ObjectId) -> dict[str, Any]:
    """Cancel your own confirmed booking before the event starts, if nothing is scanned yet."""
    now = clock.now()
    booking = bookings_repo.get_booking(booking_id)
    if booking is None or booking["userId"] != user_id:
        raise NotFoundError("Booking not found.")  # same message: don't reveal other people's bookings
    if booking["status"] != "confirmed":
        raise RuleViolation(f"This booking is already {booking['status']}.")
    if any(t["checkedInAt"] for t in booking["tickets"]):
        raise RuleViolation("Tickets that were scanned at the gate cannot be cancelled.")
    event = events_repo.get_event(booking["eventId"])
    if event is None or event["startTime"] <= now:
        raise RuleViolation("The event has already started, so this booking can no longer be cancelled.")

    new_status = "refunded" if booking["totalAmount"] > 0 else "cancelled"
    if not bookings_repo.cancel_booking(booking_id, user_id, new_status, now):
        raise RuleViolation("This booking changed a moment ago (scanned or cancelled). Please reload.")

    # Release the seats. A booking always holds ONE tier (a validator rule), so a
    # single positional $inc is enough. If bookings could span tiers we would use
    # one update with arrayFilters, e.g.
    #   {"$inc": {"ticketTiers.$[g].sold": -2, "ticketTiers.$[s].sold": -1}},
    #   array_filters=[{"g.tierId": "GOLD"}, {"s.tierId": "SILVER"}]
    if event["type"] == "workshop":
        events_repo.release_workshop_seat(event["_id"], now)
    else:
        events_repo.release_tier_seats(event["_id"], booking["tickets"][0]["tierId"],
                                       len(booking["tickets"]), now)
    return bookings_repo.get_booking(booking_id)


# --- Reading -----------------------------------------------------------------
def my_bookings(user_id: ObjectId) -> list[dict[str, Any]]:
    """The user's bookings, newest first, each with its event attached."""
    bookings = bookings_repo.list_for_user(user_id)
    events = events_repo.get_events_by_ids(list({b["eventId"] for b in bookings}))
    for booking in bookings:
        booking["event"] = events.get(booking["eventId"])
    return bookings


def booking_for_user(user_id: ObjectId, booking_id: ObjectId) -> dict[str, Any]:
    """One booking with its event and venue (for the ticket page)."""
    booking = bookings_repo.get_booking(booking_id)
    if booking is None or booking["userId"] != user_id:
        raise NotFoundError("Booking not found.")
    booking["event"] = events_repo.get_event(booking["eventId"])
    booking["venue"] = venues_repo.get_venue(booking["event"]["venueId"]) if booking["event"] else None
    return booking
