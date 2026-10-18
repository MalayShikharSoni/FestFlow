"""
Events and venues: listing, details, create/edit/cancel/delete.

Shape rules (types, enums, lengths, endTime > startTime, sold <= capacity, …)
are NOT repeated here — the collection validator enforces them and a bad write
comes back as ValidationFailed. This module only enforces rules a validator
cannot express, because a validator sees one document at a time:
  * a tier's capacity must fit its venue section   (needs the venue document)
  * no two non-cancelled events overlap at a venue  (needs other event documents)
  * a venue cannot shrink below the tiers using it  (needs event documents)
  * a venue in use cannot be deleted                (needs event documents)
  * a new event must start in the future            (needs "now"; a validator rule on
    startTime would also reject later updates to past events, e.g. marking them completed)
"""
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bson import ObjectId
from django.utils.text import slugify

from core import clock
from core.exceptions import DuplicateError, NotFoundError, RuleViolation
from repositories import bookings_repo, events_repo, users_repo, venues_repo

IST = ZoneInfo("Asia/Kolkata")
PAGE_SIZE = 9


@dataclass
class TierInput:
    tier_id: str
    name: str
    section: str
    price: int
    capacity: int

    def as_doc(self, sold: int = 0) -> dict[str, Any]:
        return {"tierId": self.tier_id, "name": self.name, "section": self.section,
                "price": self.price, "capacity": self.capacity, "sold": sold}


@dataclass
class EventInput:
    title: str
    description: str
    type: str
    venue_id: ObjectId
    start: datetime
    end: datetime
    tags: list[str] = field(default_factory=list)
    artist: str | None = None                    # pro_show
    tiers: list[TierInput] = field(default_factory=list)  # pro_show / cultural
    instructor: str | None = None                # workshop
    prerequisites: list[str] = field(default_factory=list)
    seat_limit: int | None = None


@dataclass
class VenueInput:
    name: str
    block: str
    sections: list[tuple[str, int]]  # (section name, capacity)
    facilities: list[str] = field(default_factory=list)

    def as_fields(self) -> dict[str, Any]:
        return {"name": self.name.strip(), "block": self.block.strip(),
                "sections": [{"name": n.strip(), "capacity": c} for n, c in self.sections],
                "facilities": self.facilities}


# --- Listing and details -----------------------------------------------------
def list_events(event_type: str | None = None, day: date | None = None, q: str | None = None,
                page: int = 1) -> dict[str, Any]:
    """One page of the public event list; uses $text search when `q` is given."""
    starts_from = starts_before = None
    if day:
        starts_from = datetime.combine(day, time.min, tzinfo=IST).astimezone(timezone.utc)
        starts_before = starts_from + timedelta(days=1)
    query = events_repo.build_list_filter(event_type, starts_from, starts_before)
    page = max(page, 1)
    skip = (page - 1) * PAGE_SIZE
    if q:
        total = events_repo.count_search(q, query)
        events = events_repo.search_events(q, query, skip, PAGE_SIZE)
    else:
        total = events_repo.count_events(query)
        events = events_repo.list_events(query, skip, PAGE_SIZE)
    _attach_venues(events)
    for event in events:
        _add_availability(event)
    return {"events": events, "total": total, "page": page,
            "pages": max(1, math.ceil(total / PAGE_SIZE))}


def fest_days() -> list[date]:
    """The IST calendar dates that have events, for the day filter."""
    return sorted({t.astimezone(IST).date() for t in events_repo.list_start_times()})


def get_event_detail(event_id: ObjectId) -> dict[str, Any]:
    event = events_repo.get_event(event_id)
    if event is None:
        raise NotFoundError("Event not found.")
    event["venue"] = venues_repo.get_venue(event["venueId"])
    return _add_availability(event)


def list_all_events() -> list[dict[str, Any]]:
    """Organizer table, with venue names and availability."""
    events = events_repo.list_all_events()
    _attach_venues(events)
    for event in events:
        _add_availability(event)
    return events


def _attach_venues(events: list[dict[str, Any]]) -> None:
    venues = venues_repo.get_venues_by_ids(list({e["venueId"] for e in events}))
    for event in events:
        event["venue"] = venues.get(event["venueId"])


def _add_availability(event: dict[str, Any]) -> dict[str, Any]:
    """Computed, display-only fields: seats left, % sold, cheapest price."""
    if event["type"] == "workshop":
        event["capacity"] = event["seatLimit"]
        event["seatsLeft"] = event["seatLimit"] - event["seatsTaken"]
        event["fromPrice"] = 0
    else:
        for tier in event["ticketTiers"]:
            tier["left"] = tier["capacity"] - tier["sold"]
            tier["pct"] = round(100 * tier["sold"] / tier["capacity"])
        event["capacity"] = sum(t["capacity"] for t in event["ticketTiers"])
        event["seatsLeft"] = sum(t["left"] for t in event["ticketTiers"])
        event["fromPrice"] = min(t["price"] for t in event["ticketTiers"])
    taken = event["capacity"] - event["seatsLeft"]
    event["pct"] = round(100 * taken / event["capacity"]) if event["capacity"] else 0
    return event


# --- Cross-document rules ----------------------------------------------------
def _check_tiers_fit(venue: dict[str, Any], tiers: list[dict[str, Any]]) -> None:
    """Every tier names a real section of the venue and is no bigger than it."""
    sections = {s["name"]: s["capacity"] for s in venue["sections"]}
    problems = []
    for tier in tiers:
        if tier["section"] not in sections:
            problems.append(f"{tier['tierId']}: {venue['name']} has no section '{tier['section']}'")
        elif tier["capacity"] > sections[tier["section"]]:
            problems.append(f"{tier['tierId']}: capacity {tier['capacity']} exceeds section "
                            f"'{tier['section']}' ({sections[tier['section']]} seats)")
    if problems:
        raise RuleViolation("; ".join(problems))


def _check_no_overlap(venue_id: ObjectId, start: datetime, end: datetime,
                      exclude_id: ObjectId | None = None) -> None:
    if events_repo.count_overlapping(venue_id, start, end, exclude_id):
        raise RuleViolation("Another event is already scheduled at this venue during that time.")


def _get_venue(venue_id: ObjectId) -> dict[str, Any]:
    venue = venues_repo.get_venue(venue_id)
    if venue is None:
        raise NotFoundError("Venue not found.")
    return venue


# --- Create ------------------------------------------------------------------
def create_event(data: EventInput) -> dict[str, Any]:
    now = clock.now()
    if data.start <= now:
        raise RuleViolation("A new event must start in the future.")
    venue = _get_venue(data.venue_id)
    tiers = [t.as_doc() for t in data.tiers]
    if data.type != "workshop":
        _check_tiers_fit(venue, tiers)
    _check_no_overlap(data.venue_id, data.start, data.end)

    doc: dict[str, Any] = {
        "title": data.title.strip(),
        "description": data.description.strip(),
        "type": data.type,
        "venueId": data.venue_id,
        "startTime": data.start,
        "endTime": data.end,
        "status": "scheduled",
        "tags": data.tags,
        "createdAt": now,
        "updatedAt": now,
    }
    # Only the fields of this event type are added, and empty ones are left out,
    # so the validator's oneOf reports a clear "missing artist" rather than a type error.
    if data.type == "workshop":
        doc.update(seatsTaken=0, prerequisites=data.prerequisites)
        if data.seat_limit is not None:
            doc["seatLimit"] = data.seat_limit
        if data.instructor:
            doc["instructor"] = data.instructor
    else:
        doc["ticketTiers"] = tiers
        if data.type == "pro_show" and data.artist:
            doc["artist"] = data.artist
    return events_repo.get_event(_insert_with_unique_slug(doc))


def _insert_with_unique_slug(doc: dict[str, Any]) -> ObjectId:
    """Try "my-event", then "my-event-2", ... — the unique index decides, so no race."""
    base = slugify(doc["title"])[:100] or "event"
    for n in range(1, 50):
        doc["slug"] = base if n == 1 else f"{base}-{n}"
        try:
            return events_repo.insert_event(doc)
        except DuplicateError as exc:
            if "slug" not in exc.key_value:
                raise
    raise RuleViolation("Could not find a free URL slug for this title.")


# --- Edit --------------------------------------------------------------------
def update_event(event_id: ObjectId, data: EventInput) -> dict[str, Any]:
    """Apply an edit as a series of small atomic updates.

    Each tier change is its own guarded update (price via arrayFilters, capacity
    never below `sold`, remove only if unsold, add with $push), so a booking
    happening at the same moment can never be lost or overwritten — unlike
    replacing the whole ticketTiers array. All checks run before the first write;
    if a guarded write is still refused (a booking just landed), earlier steps stay
    applied and the error says which change failed.
    """
    now = clock.now()
    event = events_repo.get_event(event_id)
    if event is None:
        raise NotFoundError("Event not found.")
    if event["status"] != "scheduled":
        raise RuleViolation(f"Only scheduled events can be edited (this one is {event['status']}).")
    if data.type != event["type"]:
        # bookings copy eventType, which is only safe because the type never changes.
        raise RuleViolation("An event's type cannot be changed.")
    venue = _get_venue(data.venue_id)
    _check_no_overlap(data.venue_id, data.start, data.end, exclude_id=event_id)

    if event["type"] == "workshop":
        tier_plan = None
    else:
        tier_plan = _plan_tier_changes(event, data.tiers)
        kept_and_new = [t.as_doc() for t in data.tiers]
        _check_tiers_fit(venue, kept_and_new)

    fields: dict[str, Any] = {
        "title": data.title.strip(), "description": data.description.strip(),
        "venueId": data.venue_id, "startTime": data.start, "endTime": data.end, "tags": data.tags,
    }
    if event["type"] == "pro_show":
        fields["artist"] = data.artist
    if event["type"] == "workshop":
        fields.update(instructor=data.instructor, prerequisites=data.prerequisites)
    events_repo.update_event_fields(event_id, fields, now)

    if event["type"] == "workshop":
        if data.seat_limit != event["seatLimit"] and not events_repo.set_seat_limit(event_id, data.seat_limit, now):
            raise RuleViolation("Seat limit cannot be lower than the seats already taken.")
    else:
        _apply_tier_changes(event_id, tier_plan, now)
    return events_repo.get_event(event_id)


def _plan_tier_changes(event: dict[str, Any], wanted: list[TierInput]) -> dict[str, Any]:
    current = {t["tierId"]: t for t in event["ticketTiers"]}
    wanted_ids = {t.tier_id for t in wanted}
    plan: dict[str, Any] = {"add": [], "remove": [], "price": [], "capacity": []}
    for tier in wanted:
        old = current.get(tier.tier_id)
        if old is None:
            plan["add"].append(tier.as_doc())
            continue
        if (tier.name, tier.section) != (old["name"], old["section"]):
            raise RuleViolation(f"{tier.tier_id}: name and section cannot change; remove the tier "
                                f"(only possible while unsold) and add a new one.")
        if tier.price != old["price"]:
            plan["price"].append((tier.tier_id, tier.price))
        if tier.capacity != old["capacity"]:
            if tier.capacity < old["sold"]:
                raise RuleViolation(f"{tier.tier_id}: capacity cannot go below the {old['sold']} already sold.")
            plan["capacity"].append((tier.tier_id, tier.capacity))
    for tier_id, old in current.items():
        if tier_id not in wanted_ids:
            if old["sold"] > 0:
                raise RuleViolation(f"{tier_id}: cannot remove a tier that has sold tickets.")
            plan["remove"].append(tier_id)
    return plan


def _apply_tier_changes(event_id: ObjectId, plan: dict[str, Any], now: datetime) -> None:
    # Add before removing, so the event never drops to zero tiers (validator: minItems 1).
    for tier in plan["add"]:
        if not events_repo.add_tier(event_id, tier, now):
            raise RuleViolation(f"{tier['tierId']}: a tier with this id already exists.")
    for tier_id, price in plan["price"]:
        events_repo.set_tier_price(event_id, tier_id, price, now)
    for tier_id, capacity in plan["capacity"]:
        if not events_repo.set_tier_capacity(event_id, tier_id, capacity, now):
            raise RuleViolation(f"{tier_id}: tickets were just sold; capacity cannot go that low.")
    for tier_id in plan["remove"]:
        if not events_repo.remove_tier(event_id, tier_id, now):
            raise RuleViolation(f"{tier_id}: a ticket was just sold, so it can no longer be removed.")


def change_tier_price(event_id: ObjectId, tier_id: str, price: int) -> None:
    if not events_repo.set_tier_price(event_id, tier_id, price, clock.now()):
        raise NotFoundError("Event or tier not found.")


# --- Cancel (soft delete) and hard delete ------------------------------------
def cancel_event(event_id: ObjectId) -> int:
    """Soft delete: mark cancelled, refund every confirmed booking, free the seats.

    Why not delete the document? Bookings point at it, and people need to see
    "cancelled, refunded" in their history; analytics (cancellation rates) and
    refund accounting need the records too. Returns the number of bookings refunded.

    Order matters: the status change comes FIRST, so no new seat claim can match
    while we refund (claims require status "scheduled").
    """
    now = clock.now()
    event = events_repo.get_event(event_id)
    if event is None:
        raise NotFoundError("Event not found.")
    if not events_repo.mark_cancelled(event_id, now):
        raise RuleViolation(f"Only scheduled events can be cancelled (this one is {event['status']}).")
    refunded = bookings_repo.refund_all_for_event(event_id, now)
    events_repo.reset_stock(event_id, event["type"] == "workshop", now)
    users_repo.pull_shifts_for_event(event_id)  # free the volunteers
    return refunded


def delete_event(event_id: ObjectId) -> None:
    """Hard delete, allowed only when the event has no bookings at all.

    Count and delete are two operations, so a booking landing in between would be
    orphaned; a transaction (replica set) would close that gap, and verify_data's
    consistency check would flag it. Events with bookings are cancelled instead.
    """
    count = bookings_repo.count_for_event(event_id)
    if count:
        raise RuleViolation(f"This event has {count} booking(s); cancel it instead of deleting.")
    if not events_repo.delete_event(event_id):
        raise NotFoundError("Event not found.")
    users_repo.pull_shifts_for_event(event_id)


# --- Venues ------------------------------------------------------------------
def list_venues() -> list[dict[str, Any]]:
    return venues_repo.list_venues()


def create_venue(data: VenueInput) -> dict[str, Any]:
    """Raises DuplicateError if the name is taken, ValidationFailed for a bad shape."""
    doc = {**data.as_fields(), "createdAt": clock.now()}
    return venues_repo.get_venue(venues_repo.insert_venue(doc))


def update_venue(venue_id: ObjectId, data: VenueInput) -> dict[str, Any]:
    fields = data.as_fields()
    _check_sections_still_fit(venue_id, fields)
    if not venues_repo.update_venue(venue_id, fields):
        raise NotFoundError("Venue not found.")
    return venues_repo.get_venue(venue_id)


def _check_sections_still_fit(venue_id: ObjectId, fields: dict[str, Any]) -> None:
    """Cross-collection: every tier of the venue's active events must still fit."""
    for event in events_repo.list_active_for_venue(venue_id):
        try:
            _check_tiers_fit(fields, event.get("ticketTiers", []))
        except RuleViolation as exc:
            raise RuleViolation(f"{event['title']}: {exc}") from exc


def delete_venue(venue_id: ObjectId) -> None:
    count = events_repo.count_for_venue(venue_id)
    if count:
        raise RuleViolation(f"{count} event(s) use this venue, so it cannot be deleted.")
    if not venues_repo.delete_venue(venue_id):
        raise NotFoundError("Venue not found.")


def import_venues(rows: list[VenueInput]) -> tuple[int, int]:
    """Idempotent bulk import via upsert by name. Returns (created, updated)."""
    created = updated = 0
    now = clock.now()
    for row in rows:
        fields = row.as_fields()
        existing = venues_repo.get_venue_by_name(fields["name"])
        if existing:
            _check_sections_still_fit(existing["_id"], fields)
        if venues_repo.upsert_venue_by_name(fields, now):
            created += 1
        else:
            updated += 1
    return created, updated
