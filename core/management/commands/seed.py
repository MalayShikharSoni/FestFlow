"""
python manage.py seed [--reset] [--bookings N] [--seed S] [--db NAME]

Builds a realistic Spectra 2026 dataset in Python, inserts it (through the
validators — nothing is bypassed), then runs verify_data.

Timeline, relative to the moment you run the seed:
    fest days 1-3 = the three days before today -> events "completed", with check-ins
    today         = deliberately left empty (a mid-fest break), so every event is
                    clearly finished or clearly in the future, whatever the hour
    fest days 4-5 = tomorrow and the day after  -> events "scheduled", still bookable
Re-seeding later moves the fest along with the clock, so the demo always has past
analytics AND future events to book.

Reproducible: all randomness comes from one random.Random(seed), and Faker is
seeded with the same number. Re-running on the same day gives the same people,
events and bookings (only the ObjectIds differ).

How bookings are generated (BookingSimulator):
  1. Draw N booking "intents" (who, which event/tier, how many, when, cancels?).
  2. Size every tier's capacity from that demand, capped by the real venue
     section capacity. This keeps occupancy realistic at both 5,000 and 50,000
     bookings; about 30% of past tiers get less room than demand and sell out.
  3. Replay the intents first-come-first-served. A confirmed booking must claim
     seats (if its tier is full it tries another tier of the same event, else it
     is turned away); a booking that is later cancelled holds no seats at the end.
     Intents dated after "now" have not happened yet and are skipped. Fresh
     intents are drawn until exactly N bookings exist.
  So each tier's `sold` equals its confirmed tickets by construction, and
  verify_data proves it against the database afterwards.
"""
import random
import string
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import accumulate
from typing import Any
from zoneinfo import ZoneInfo

from bson import ObjectId
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from faker import Faker
from pymongo import InsertOne
from pymongo.collection import Collection
from pymongo.errors import BulkWriteError

from core import seed_catalog as cat
from core.db import get_client
from core.exceptions import describe_err_info
from core.management.commands.init_db import init_database

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

TOTAL_USERS = 1500
FAKE_ORGANIZERS = 2   # + the demo organizer = 3
FAKE_VOLUNTEERS = 39  # + the demo volunteer = 40
WORKSHOP_MAX_SEATS = 200
CODE_ALPHABET = string.ascii_uppercase + string.digits


# --- Small helpers -----------------------------------------------------------
class Picker:
    """Weighted random choice. Cumulative weights make each pick O(log n)."""

    def __init__(self, rng: random.Random, items: list[Any], weights: list[float]):
        self.rng = rng
        self.items = items
        self.cum_weights = list(accumulate(weights))

    def pick(self) -> Any:
        return self.rng.choices(self.items, cum_weights=self.cum_weights)[0]


def whole_seconds(dt: datetime) -> datetime:
    """BSON dates keep milliseconds; whole seconds keep generated data tidy."""
    return dt.replace(microsecond=0)


def round_up(value: float, step: int) -> int:
    return int(-(-value // step) * step)


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def fest_dates(now: datetime) -> dict[int, date]:
    """Fest day number -> IST calendar date (see the timeline in the module docstring)."""
    today = now.astimezone(IST).date()
    offsets = {1: -3, 2: -2, 3: -1, 4: 1, 5: 2}
    return {day: today + timedelta(days=offset) for day, offset in offsets.items()}


def ist_to_utc(day: date, hhmm: str) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST).astimezone(UTC)


# --- Venues and events -------------------------------------------------------
def build_venues(created_at: datetime) -> dict[str, dict[str, Any]]:
    venues = {}
    for key, spec in cat.VENUES.items():
        venues[key] = {
            "_id": ObjectId(),
            "name": spec["name"],
            "block": spec["block"],
            "sections": [{"name": name, "capacity": cap} for name, cap in spec["sections"]],
            "facilities": list(spec["facilities"]),
            "createdAt": created_at,
        }
    return venues


@dataclass
class EventPlan:
    """An event document plus the seed-only facts used to simulate its sales."""

    doc: dict[str, Any]
    venue: dict[str, Any]
    popularity: float
    tier_weights: list[float]
    cancel_at: datetime | None  # only set for the event the seed cancels

    @property
    def is_workshop(self) -> bool:
        return self.doc["type"] == "workshop"

    @property
    def start(self) -> datetime:
        return self.doc["startTime"]

    @property
    def tiers(self) -> list[dict[str, Any]]:
        return self.doc.get("ticketTiers", [])

    @property
    def is_past(self) -> bool:
        return self.doc["status"] == "completed"

    def tier_index(self, tier_id: str) -> int:
        return next(i for i, t in enumerate(self.tiers) if t["tierId"] == tier_id)

    def section_capacity(self, section: str) -> int:
        return next(s["capacity"] for s in self.venue["sections"] if s["name"] == section)


def build_events(venues: dict[str, dict[str, Any]], now: datetime, created_at: datetime) -> list[EventPlan]:
    dates = fest_dates(now)
    plans = []
    for spec in cat.EVENTS:
        venue = venues[spec["venue"]]
        start = ist_to_utc(dates[spec["day"]], spec["start"])
        end = start + timedelta(hours=spec["hours"])
        doc: dict[str, Any] = {
            "_id": ObjectId(),
            "title": spec["title"],
            "slug": spec["slug"],
            "description": spec["description"],
            "type": spec["type"],
            "venueId": venue["_id"],
            "startTime": start,
            "endTime": end,
            "status": "completed" if end <= now else "scheduled",
            "tags": list(spec["tags"]),
            "createdAt": created_at,
            "updatedAt": created_at,
        }
        tier_weights: list[float] = []
        if spec["type"] == "workshop":
            # seatLimit is sized from demand later (size_capacities).
            doc.update(seatLimit=0, seatsTaken=0, instructor=spec["instructor"],
                       prerequisites=list(spec["prerequisites"]))
        else:
            if spec["type"] == "pro_show":
                doc["artist"] = spec["artist"]
            doc["ticketTiers"] = [
                {"tierId": tier_id, "name": name, "section": section, "price": price,
                 "capacity": 0, "sold": 0}
                for tier_id, name, section, price, _ in spec["tiers"]
            ]
            tier_weights = [weight for *_, weight in spec["tiers"]]
        # The cancelled event was called off 8 hours before the seed ran.
        cancel_at = whole_seconds(now - timedelta(hours=8)) if spec.get("cancelled") else None
        plans.append(EventPlan(doc, venue, spec["popularity"], tier_weights, cancel_at))
    return plans


# --- Users -------------------------------------------------------------------
@dataclass
class UserTraits:
    """Hidden per-user behaviour (never stored): how often they book and how flaky they are."""

    activity: float
    flakiness: float


def random_phone(rng: random.Random) -> str:
    return str(rng.randint(6, 9)) + "".join(rng.choices(string.digits, k=9))


def build_users(rng: random.Random, fake: Faker, created_from: datetime, created_to: datetime
                ) -> tuple[list[dict[str, Any]], dict[ObjectId, UserTraits]]:
    # Hashing is deliberately slow (PBKDF2, ~1M iterations), so 1,500 separate
    # hashes would take minutes. Fake users share one hash of the demo password;
    # the demo accounts get their own. Every seeded user can log in with it.
    shared_hash = make_password(cat.DEMO_PASSWORD)
    college_picker = Picker(rng, [c for c, _ in cat.COLLEGES], [w for _, w in cat.COLLEGES])
    domain_picker = Picker(rng, [d for d, _ in cat.EMAIL_DOMAINS], [w for _, w in cat.EMAIL_DOMAINS])
    span = (created_to - created_from).total_seconds()

    users: list[dict[str, Any]] = []
    for account in cat.DEMO_ACCOUNTS:
        users.append({
            "_id": ObjectId(), "name": account["name"], "email": account["email"],
            "phone": random_phone(rng), "role": account["role"],
            "passwordHash": make_password(cat.DEMO_PASSWORD), "college": cat.HOME_COLLEGE,
            "shifts": [], "createdAt": created_from,
        })

    emails = {u["email"] for u in users}
    fake_attendees = TOTAL_USERS - len(users) - FAKE_ORGANIZERS - FAKE_VOLUNTEERS
    roles = ["organizer"] * FAKE_ORGANIZERS + ["volunteer"] * FAKE_VOLUNTEERS + ["attendee"] * fake_attendees
    for role in roles:
        first, last = fake.first_name(), fake.last_name()
        email = ""
        while not email or email in emails:
            local = "".join(ch for ch in f"{first}.{last}{rng.randint(1, 999)}".lower() if ch.isalnum() or ch == ".")
            email = f"{local}@{domain_picker.pick()}"
        emails.add(email)
        college = college_picker.pick()
        user = {
            "_id": ObjectId(), "name": f"{first} {last}", "email": email,
            "phone": random_phone(rng), "role": role, "passwordHash": shared_hash,
            "college": college, "shifts": [],
            "createdAt": whole_seconds(created_from + timedelta(seconds=rng.uniform(0, span))),
        }
        if college == cat.HOME_COLLEGE:
            user["regNo"] = str(rng.randint(11800000, 12499999))  # LPU-style 8-digit number
        users.append(user)

    traits = {}
    for user in users:
        flaky = rng.uniform(0.15, 0.35) if rng.random() < 0.1 else rng.uniform(0.0, 0.08)
        # Pareto: most people book once or twice, a few book a lot (top spenders).
        traits[user["_id"]] = UserTraits(activity=rng.paretovariate(1.8), flakiness=flaky)
    return users, traits


# --- Volunteer shifts --------------------------------------------------------
def make_shift(rng: random.Random, plan: EventPlan, duty: str) -> dict[str, Any]:
    """A shift always lies inside its event's time window (a service-level rule)."""
    start, end = plan.start, plan.doc["endTime"]
    mid = start + (end - start) / 2
    roll = rng.random()
    window = (start, end) if roll < 0.5 else (start, mid) if roll < 0.75 else (mid, end)
    return {"eventId": plan.doc["_id"], "duty": duty, "start": window[0], "end": window[1]}


def overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


def assign_shifts(rng: random.Random, volunteers: list[dict[str, Any]], plans: list[EventPlan],
                  demo_volunteer: dict[str, Any]) -> None:
    duty_picker = Picker(rng, [d for d, _ in cat.DUTIES], [w for _, w in cat.DUTIES])
    wanted = {"pro_show": 6, "cultural": 3, "workshop": 2}

    def try_assign(volunteer: dict[str, Any], plan: EventPlan) -> bool:
        shift = make_shift(rng, plan, duty_picker.pick())
        if len(volunteer["shifts"]) >= 5 or any(overlaps(shift, s) for s in volunteer["shifts"]):
            return False
        volunteer["shifts"].append(shift)
        return True

    for plan in plans:
        if plan.cancel_at:
            continue
        candidates = volunteers[:]
        rng.shuffle(candidates)
        # Give the demo volunteer the upcoming pro shows so their schedule shows something.
        if plan.doc["type"] == "pro_show" and not plan.is_past:
            candidates.remove(demo_volunteer)
            candidates.insert(0, demo_volunteer)
        assigned = 0
        for volunteer in candidates:
            if assigned == wanted[plan.doc["type"]]:
                break
            assigned += try_assign(volunteer, plan)

    # Every volunteer gets at least one shift.
    open_plans = [p for p in plans if not p.cancel_at]
    for volunteer in volunteers:
        while not volunteer["shifts"]:
            try_assign(volunteer, rng.choice(open_plans))
        volunteer["shifts"].sort(key=lambda s: s["start"])


# --- Bookings ----------------------------------------------------------------
@dataclass
class Intent:
    """Someone wanting to book: may succeed, be turned away (sold out) or not have happened yet."""

    user_id: ObjectId
    plan: EventPlan
    tier_index: int | None  # None for workshops
    qty: int
    booked_at: datetime
    cancel_at: datetime | None = None
    force_show: bool | None = None  # demo bookings fix whether the attendee turned up


def no_show_probability(plan: EventPlan, price: int, lead_days: float, flakiness: float) -> float:
    """The patterns the analytics (and the optional ML model) should find.

    Free and cheap tickets, and bookings made long in advance, are more likely
    to be wasted; paid pro-show tickets almost always get used.
    """
    p = {"pro_show": 0.03, "cultural": 0.08, "workshop": 0.10}[plan.doc["type"]]
    if price == 0:
        p += 0.18
    elif price < 200:
        p += 0.06
    if lead_days > 14:
        p += 0.12
    elif lead_days > 7:
        p += 0.05
    return min(p + flakiness, 0.9)


class BookingSimulator:
    def __init__(self, rng: random.Random, now: datetime, plans: list[EventPlan],
                 bookers: list[dict[str, Any]], traits: dict[ObjectId, UserTraits]):
        self.rng = rng
        self.now = now
        self.plans = plans
        self.traits = traits
        self.event_picker = Picker(rng, plans, [p.popularity for p in plans])
        self.user_picker = Picker(rng, [u["_id"] for u in bookers],
                                  [traits[u["_id"]].activity for u in bookers])
        self.lead_picker = Picker(rng, [(lo, hi) for lo, hi, _ in cat.LEAD_TIME_BUCKETS],
                                  [w for *_, w in cat.LEAD_TIME_BUCKETS])
        self.qty_picker = Picker(rng, [q for q, _ in cat.QUANTITIES], [w for _, w in cat.QUANTITIES])
        self.payment_picker = Picker(rng, [m for m, _ in cat.PAYMENT_MODES], [w for _, w in cat.PAYMENT_MODES])
        self.tier_pickers = {id(p): Picker(rng, list(range(len(p.tiers))), p.tier_weights)
                             for p in plans if not p.is_workshop}
        self.codes: set[str] = set()
        self.bookings: list[dict[str, Any]] = []
        self.sold_out = 0
        self.not_yet = 0
        self.last_activity: dict[ObjectId, datetime] = {}

    # 1. Demand ---------------------------------------------------------------
    def random_intent(self) -> Intent:
        plan = self.event_picker.pick()
        tier_index = None
        qty = 1
        if not plan.is_workshop:
            tier_index = self.tier_pickers[id(plan)].pick()
            qty = self.qty_picker.pick()
        low, high = self.lead_picker.pick()
        lead_days = self.rng.uniform(low, high)
        booked_at = whole_seconds(plan.start - timedelta(days=lead_days))

        # ~6% cancel, more among early bookers; only possible before the event starts.
        cancel_at = None
        p_cancel = 0.06 + (0.05 if lead_days > 7 else 0.0)
        last_moment = min(plan.start - timedelta(minutes=30), self.now, plan.cancel_at or self.now)
        if self.rng.random() < p_cancel and last_moment - booked_at > timedelta(minutes=15):
            cancel_at = whole_seconds(booked_at + (last_moment - booked_at) * self.rng.uniform(0.05, 1.0))
        return Intent(self.user_picker.pick(), plan, tier_index, qty, booked_at, cancel_at)

    def has_happened(self, intent: Intent) -> bool:
        """False for intents dated after now (or after their event was called off)."""
        plan = intent.plan
        return intent.booked_at <= self.now and not (plan.cancel_at and intent.booked_at > plan.cancel_at)

    def draw_demand(self, target: int) -> list[Intent]:
        """Draw intents until `target` of them have already happened.

        The extra ones (dated in the future) still count as demand for future
        events, which is what makes their capacity larger than their sales so far.
        """
        intents: list[Intent] = []
        happened = 0
        while happened < target:
            intent = self.random_intent()
            intents.append(intent)
            happened += self.has_happened(intent)
        return intents

    # 2. Capacity -------------------------------------------------------------
    def size_capacities(self, demand_intents: list[Intent]) -> None:
        """Give each tier room for roughly its demand, never more than its venue section holds."""
        demand: Counter = Counter()
        for intent in demand_intents:
            if intent.cancel_at is None:
                demand[(intent.plan.doc["slug"], intent.tier_index)] += intent.qty
        for plan in self.plans:
            slug = plan.doc["slug"]
            if plan.is_workshop:
                limit = min(WORKSHOP_MAX_SEATS, sum(s["capacity"] for s in plan.venue["sections"]))
                want = demand[(slug, None)] * self._headroom(plan)
                plan.doc["seatLimit"] = clamp(round_up(want, 5), 15, limit)
                continue
            for i, tier in enumerate(plan.tiers):
                want = demand[(slug, i)] * self._headroom(plan)
                tier["capacity"] = clamp(round_up(want, 10), 40, plan.section_capacity(tier["section"]))

    def _headroom(self, plan: EventPlan) -> float:
        if not plan.is_past:
            return self.rng.uniform(1.1, 1.6)  # sales still open: room to book in the demo
        if self.rng.random() < 0.3:
            return self.rng.uniform(0.8, 0.97)  # demand exceeds supply: sells out
        return self.rng.uniform(1.05, 1.5)

    # 3. Replay ---------------------------------------------------------------
    def run(self, target: int, first_intents: list[Intent]) -> None:
        queue = deque(first_intents)
        attempts = 0
        while len(self.bookings) < target and attempts < target * 6:
            attempts += 1
            intent = queue.popleft() if queue else self.random_intent()
            self._try_book(intent)

    def _try_book(self, intent: Intent) -> None:
        plan = intent.plan
        if not self.has_happened(intent):
            self.not_yet += 1
            return
        tier_index = intent.tier_index
        if intent.cancel_at is None:  # a booking that stays confirmed needs seats
            claimed, tier_index = self._claim_seats(plan, tier_index, intent.qty)
            if not claimed:
                self.sold_out += 1
                return
        self.bookings.append(self._booking_doc(intent, tier_index))
        latest = intent.cancel_at or intent.booked_at
        self.last_activity[plan.doc["_id"]] = max(latest, self.last_activity.get(plan.doc["_id"], latest))

    def _claim_seats(self, plan: EventPlan, tier_index: int | None, qty: int) -> tuple[bool, int | None]:
        if plan.is_workshop:
            if plan.doc["seatsTaken"] + 1 > plan.doc["seatLimit"]:
                return False, None
            plan.doc["seatsTaken"] += 1
            return True, None
        # Wanted tier first; if it is full the buyer settles for another tier with room.
        others = [i for i in range(len(plan.tiers)) if i != tier_index]
        self.rng.shuffle(others)
        for i in [tier_index] + others:
            tier = plan.tiers[i]
            if tier["sold"] + qty <= tier["capacity"]:
                tier["sold"] += qty
                return True, i
        return False, None

    def _new_code(self) -> str:
        while True:
            code = "SPX-" + "".join(self.rng.choices(CODE_ALPHABET, k=8))
            if code not in self.codes:
                self.codes.add(code)
                return code

    def _booking_doc(self, intent: Intent, tier_index: int | None) -> dict[str, Any]:
        plan = intent.plan
        tier = plan.tiers[tier_index] if tier_index is not None else None
        price = tier["price"] if tier else 0  # workshop registration is free
        total = price * intent.qty
        tickets = [{"code": self._new_code(), "tierId": tier["tierId"] if tier else None,
                    "price": price, "checkedInAt": None} for _ in range(intent.qty)]
        status = "confirmed"
        if intent.cancel_at:
            status = "refunded" if total > 0 else "cancelled"
        if status == "confirmed" and plan.is_past:
            self._check_in(intent, tickets, price)
        return {
            "_id": ObjectId(),
            "userId": intent.user_id,
            "eventId": plan.doc["_id"],
            "eventType": plan.doc["type"],
            "status": status,
            "paymentMode": "FREE" if total == 0 else self.payment_picker.pick(),
            "tickets": tickets,
            "totalAmount": total,
            "bookedAt": intent.booked_at,
            "cancelledAt": intent.cancel_at,
        }

    def _check_in(self, intent: Intent, tickets: list[dict[str, Any]], price: int) -> None:
        """Gates open an hour early: arrivals cluster 0-60 min before start, a few come late."""
        lead_days = (intent.plan.start - intent.booked_at).total_seconds() / 86400
        if intent.force_show is None:
            p_no_show = no_show_probability(intent.plan, price, lead_days, self.traits[intent.user_id].flakiness)
            shows_up = self.rng.random() >= p_no_show
        else:
            shows_up = intent.force_show
        if not shows_up:
            return
        arrival = intent.plan.start - timedelta(minutes=self.rng.triangular(-15, 60, 15))
        for i, ticket in enumerate(tickets):
            if i > 0 and self.rng.random() < 0.05:
                continue  # one friend in the group did not make it
            ticket["checkedInAt"] = whole_seconds(arrival + timedelta(minutes=self.rng.uniform(0, 3)))

    # 4. Finishing touches ----------------------------------------------------
    def cancel_called_off_events(self) -> None:
        """The cancelled event: every still-confirmed booking is cancelled/refunded and seats released."""
        cancelled = {p.doc["_id"]: p for p in self.plans if p.cancel_at}
        for booking in self.bookings:
            plan = cancelled.get(booking["eventId"])
            if plan is None or booking["status"] != "confirmed":
                continue
            booking["status"] = "refunded" if booking["totalAmount"] > 0 else "cancelled"
            booking["cancelledAt"] = plan.cancel_at
            if plan.is_workshop:
                plan.doc["seatsTaken"] -= 1
            else:
                plan.tiers[plan.tier_index(booking["tickets"][0]["tierId"])]["sold"] -= len(booking["tickets"])
        for plan in cancelled.values():
            plan.doc["status"] = "cancelled"
            plan.doc["updatedAt"] = plan.cancel_at

    def make_demo_tier_sold_out(self, slug: str, tier_id: str) -> None:
        plan = next(p for p in self.plans if p.doc["slug"] == slug)
        tier = plan.tiers[plan.tier_index(tier_id)]
        if tier["sold"] > 0:
            tier["capacity"] = tier["sold"]

    def stamp_updated_at(self) -> None:
        for plan in self.plans:
            if not plan.cancel_at and plan.doc["_id"] in self.last_activity:
                plan.doc["updatedAt"] = max(plan.doc["createdAt"], self.last_activity[plan.doc["_id"]])


def demo_attendee_intents(attendee_id: ObjectId, plans: dict[str, EventPlan], now: datetime) -> list[Intent]:
    """Fixed bookings for attendee@spectra.test so 'My bookings' shows every status."""

    def booked(plan: EventPlan, days_before: float) -> datetime:
        return whole_seconds(min(plan.start - timedelta(days=days_before), now - timedelta(hours=1)))

    bollywood, dance = plans["bollywood-night-aria-sen"], plans["street-dance-battle"]
    photo, finale = plans["photography-masterclass"], plans["grand-finale-bass-theory"]
    hacking = plans["ethical-hacking-basics"]
    photo_booked = booked(photo, 9)
    return [
        Intent(attendee_id, bollywood, bollywood.tier_index("GOLD"), 2, booked(bollywood, 6), force_show=True),
        Intent(attendee_id, dance, dance.tier_index("FREE"), 1, booked(dance, 10), force_show=False),
        Intent(attendee_id, photo, None, 1, photo_booked, cancel_at=photo_booked + timedelta(days=2)),
        Intent(attendee_id, finale, finale.tier_index("SILVER"), 2, booked(finale, 3)),
        Intent(attendee_id, hacking, None, 1, booked(hacking, 2)),
    ]


# --- Inserting -----------------------------------------------------------------
def insert_documents(coll: Collection, docs: list[dict[str, Any]], use_bulk_write: bool = False) -> None:
    """insert_many for most collections; bulk_write for bookings (both batch automatically).

    ordered=False lets the server continue past a bad document, so one run
    reports every failure instead of only the first.
    """
    try:
        if use_bulk_write:
            coll.bulk_write([InsertOne(doc) for doc in docs], ordered=False)
        else:
            coll.insert_many(docs, ordered=False)
    except BulkWriteError as exc:
        errors = exc.details.get("writeErrors", [])
        lines = [f"{len(errors)} {coll.name} document(s) were rejected. First problems:"]
        for err in errors[:3]:
            lines.append(f"  code {err['code']}: {describe_err_info(coll.name, err.get('errInfo', {}))}")
        raise CommandError("\n".join(lines)) from exc


# --- Summary -----------------------------------------------------------------
def pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def summary_lines(plans: list[EventPlan], users: list[dict[str, Any]], bookings: list[dict[str, Any]],
                  sim: BookingSimulator) -> list[str]:
    types = Counter(p.doc["type"] for p in plans)
    statuses = Counter(p.doc["status"] for p in plans)
    roles = Counter(u["role"] for u in users)
    b_status = Counter(b["status"] for b in bookings)
    confirmed = [b for b in bookings if b["status"] == "confirmed"]
    revenue = sum(b["totalAmount"] for b in confirmed)
    past_ids = {p.doc["_id"] for p in plans if p.is_past}
    past_tickets = [t for b in confirmed if b["eventId"] in past_ids for t in b["tickets"]]
    checked_in = sum(1 for t in past_tickets if t["checkedInAt"])
    paid = [b for b in bookings if b["totalAmount"] > 0]
    modes = Counter(b["paymentMode"] for b in paid)
    starts = {p.doc["_id"]: p.start for p in plans}
    leads = Counter()
    for b in bookings:
        days = (starts[b["eventId"]] - b["bookedAt"]).total_seconds() / 86400
        leads[next((f"<{hi}d" for hi in (1, 3, 7, 14, 30) if days < hi), "30d+")] += 1
    tiers = [t for p in plans for t in p.tiers]
    sold_out = sum(1 for t in tiers if t["sold"] == t["capacity"])
    seats = sum(t["capacity"] for t in tiers)
    return [
        f"  events   {len(plans)}: {dict(types)} | {dict(statuses)}",
        f"  users    {len(users)}: {dict(roles)}",
        f"  bookings {len(bookings)}: {dict(b_status)} -> cancellation rate {pct(len(bookings) - len(confirmed), len(bookings))}",
        f"  confirmed tickets {sum(len(b['tickets']) for b in confirmed)}, revenue Rs. {revenue:,}",
        f"  ticketed occupancy {pct(sum(t['sold'] for t in tiers), seats)} of {seats:,} seats; "
        f"{sold_out}/{len(tiers)} tiers sold out",
        f"  completed events: {checked_in}/{len(past_tickets)} tickets checked in -> "
        f"no-show rate {pct(len(past_tickets) - checked_in, len(past_tickets))}",
        "  payment mix (paid): " + ", ".join(f"{m} {pct(n, len(paid))}" for m, n in modes.most_common()),
        "  lead time: " + ", ".join(f"{k} {pct(leads[k], len(bookings))}"
                                    for k in ("<1d", "<3d", "<7d", "<14d", "<30d", "30d+")),
        f"  turned away: {sim.sold_out} sold out, {sim.not_yet} not booked yet (intent dated after now)",
    ]


# --- Command -----------------------------------------------------------------
class Command(BaseCommand):
    help = "Generate and insert the Spectra 2026 demo dataset, then run verify_data."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--reset", action="store_true", help="Drop the database first, then run init_db.")
        parser.add_argument("--bookings", type=int, default=5000, help="Number of bookings (default 5000).")
        parser.add_argument("--seed", type=int, default=2026, help="Random seed (default 2026).")
        parser.add_argument("--db", default=settings.MONGO_DB_NAME, help="Database name.")

    def handle(self, *args: Any, **options: Any) -> None:
        started = time.perf_counter()
        target = options["bookings"]
        if target < 10:
            raise CommandError("--bookings must be at least 10")
        db_name = options["db"]
        client = get_client()
        if options["reset"]:
            client.drop_database(db_name)
            self.stdout.write(f"Dropped database '{db_name}'.")
        db = client[db_name]
        init_database(db)
        if any(db[name].estimated_document_count() for name in ("venues", "events", "users", "bookings")):
            raise CommandError(f"Database '{db_name}' already has data. Re-run with --reset to wipe and reseed.")

        rng = random.Random(options["seed"])
        fake = Faker("en_IN")
        fake.seed_instance(options["seed"])
        now = whole_seconds(datetime.now(UTC))
        dates = fest_dates(now)
        catalogue_created = ist_to_utc(dates[1], "09:00") - timedelta(days=50)

        self.stdout.write("Generating data...")
        venues = build_venues(catalogue_created)
        plans = build_events(venues, now, catalogue_created)
        users, traits = build_users(rng, fake, catalogue_created - timedelta(days=70),
                                    catalogue_created - timedelta(days=1))
        by_email = {u["email"]: u for u in users}
        demo_ids = {by_email[a["email"]]["_id"] for a in cat.DEMO_ACCOUNTS}
        volunteers = [u for u in users if u["role"] == "volunteer"]
        assign_shifts(rng, volunteers, plans, by_email["volunteer@spectra.test"])

        bookers = [u for u in users if u["role"] in ("attendee", "volunteer") and u["_id"] not in demo_ids]
        sim = BookingSimulator(rng, now, plans, bookers, traits)
        demand = sim.draw_demand(target)
        sim.size_capacities(demand)
        demand.sort(key=lambda i: i.booked_at)  # first come, first served
        plans_by_slug = {p.doc["slug"]: p for p in plans}
        demo = demo_attendee_intents(by_email["attendee@spectra.test"]["_id"], plans_by_slug, now)
        sim.run(target, demo + demand)
        if len(sim.bookings) < target:
            self.stdout.write(self.style.WARNING(
                f"Only {len(sim.bookings)} bookings fit in the venues' capacity (asked for {target})."))
        sim.cancel_called_off_events()
        sim.make_demo_tier_sold_out(*cat.DEMO_SOLD_OUT_TIER)
        sim.stamp_updated_at()

        self.stdout.write("Inserting...")
        insert_documents(db.venues, list(venues.values()))
        insert_documents(db.events, [p.doc for p in plans])
        insert_documents(db.users, users)
        insert_documents(db.bookings, sim.bookings, use_bulk_write=True)

        elapsed = time.perf_counter() - started
        day = {d: dates[d].strftime("%d %b") for d in dates}
        self.stdout.write(self.style.SUCCESS(
            f"Seeded '{db_name}' in {elapsed:.1f}s (seed {options['seed']}). Fest days 1-3: "
            f"{day[1]} - {day[3]} (completed), days 4-5: {day[4]} - {day[5]} (scheduled)."))
        for line in summary_lines(plans, users, sim.bookings, sim):
            self.stdout.write(line)
        self.stdout.write(f"  demo logins (password {cat.DEMO_PASSWORD}): "
                          + ", ".join(a["email"] for a in cat.DEMO_ACCOUNTS))
        self.stdout.write("")
        call_command("verify_data", db=db_name, stdout=self.stdout)
