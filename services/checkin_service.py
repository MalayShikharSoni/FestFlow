"""
Gate check-in.

The write is ONE atomic find_one_and_update (bookings_repo.check_in_ticket) that
only matches an unused ticket of a confirmed booking. Only when it does not
match do we read the booking to explain why — so scanning the same code twice,
even at two gates at the same instant, can never count the person twice.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core import clock
from repositories import bookings_repo, events_repo, users_repo

CODE_PATTERN = re.compile(r"^SPX-[A-Z0-9]{8}$")

# Outcome -> Bootstrap colour for the result banner.
COLOURS = {"ok": "success", "already": "warning", "cancelled": "danger", "invalid": "danger"}


@dataclass
class CheckInResult:
    outcome: str  # "ok" | "already" | "cancelled" | "invalid"
    message: str
    code: str
    holder: str | None = None
    event_title: str | None = None
    checked_in_at: datetime | None = None

    @property
    def colour(self) -> str:
        return COLOURS[self.outcome]


def check_in(raw_code: str) -> CheckInResult:
    code = raw_code.strip().upper()
    if not CODE_PATTERN.match(code):
        return CheckInResult("invalid", "Not a Spectra ticket code (expected SPX-XXXXXXXX).", code)

    now = clock.now()
    booking = bookings_repo.check_in_ticket(code, now)
    if booking is not None:
        return _with_details(CheckInResult("ok", "Welcome! Ticket checked in.", code, checked_in_at=now), booking)

    # The atomic update matched nothing. One follow-up read tells the cases apart.
    booking = bookings_repo.find_by_ticket_code(code)
    if booking is None:
        return CheckInResult("invalid", "No ticket with this code exists.", code)
    if booking["status"] != "confirmed":
        return _with_details(CheckInResult("cancelled", f"This booking was {booking['status']}. Do not admit.", code),
                             booking)
    ticket = next(t for t in booking["tickets"] if t["code"] == code)
    result = CheckInResult("already", "This ticket was already used.", code, checked_in_at=ticket["checkedInAt"])
    return _with_details(result, booking)


def _with_details(result: CheckInResult, booking: dict[str, Any]) -> CheckInResult:
    user = users_repo.get_user(booking["userId"])
    event = events_repo.get_event(booking["eventId"])
    result.holder = user["name"] if user else None
    result.event_title = event["title"] if event else None
    return result
