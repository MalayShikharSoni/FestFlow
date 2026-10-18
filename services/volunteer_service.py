"""
Volunteer shift scheduling. Shifts are embedded in the volunteer's user document.

The validator checks each shift on its own (duty enum, end > start, only
volunteers have shifts). Two rules need OTHER documents, so they live here:
  * the shift must lie inside its event's time window (needs the event), and
  * a volunteer's shifts must not overlap (needs the other shifts — enforced
    atomically in the filter of users_repo.push_shift).
"""
from datetime import datetime
from typing import Any

from bson import ObjectId

from core.exceptions import NotFoundError, RuleViolation
from core.schemas import DUTIES
from repositories import events_repo, users_repo


def list_volunteers() -> list[dict[str, Any]]:
    """Volunteers with their shifts, each shift carrying its event title."""
    volunteers = users_repo.list_volunteers()
    event_ids = list({s["eventId"] for v in volunteers for s in v["shifts"]})
    events = events_repo.get_events_by_ids(event_ids)
    for volunteer in volunteers:
        for shift in volunteer["shifts"]:
            event = events.get(shift["eventId"])
            shift["eventTitle"] = event["title"] if event else "(deleted event)"
            shift["eventIdStr"] = str(shift["eventId"])
    return volunteers


def assign_shift(user_id: ObjectId, event_id: ObjectId, duty: str,
                 start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    """Add a shift; defaults to the whole event if start/end are not given."""
    if duty not in DUTIES:
        raise RuleViolation(f"Duty must be one of: {', '.join(DUTIES)}.")
    volunteer = users_repo.get_user(user_id)
    if volunteer is None or volunteer["role"] != "volunteer":
        raise NotFoundError("Volunteer not found.")
    event = events_repo.get_event(event_id)
    if event is None:
        raise NotFoundError("Event not found.")
    if event["status"] == "cancelled":
        raise RuleViolation("This event is cancelled.")

    start = start or event["startTime"]
    end = end or event["endTime"]
    # Cross-collection rule: the validator on `users` cannot see the event's times.
    if start < event["startTime"] or end > event["endTime"]:
        raise RuleViolation("A shift must lie inside the event's start and end time.")

    shift = {"eventId": event_id, "duty": duty, "start": start, "end": end}
    if not users_repo.push_shift(user_id, shift):
        raise RuleViolation(f"{volunteer['name']} already has a shift overlapping this time.")
    return shift


def remove_shift(user_id: ObjectId, event_id: ObjectId, start: datetime) -> None:
    if not users_repo.pull_shift(user_id, event_id, start):
        raise NotFoundError("Shift not found.")
