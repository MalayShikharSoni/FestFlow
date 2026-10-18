"""
python manage.py verify_data [--db NAME]

Checks invariants the validators cannot enforce on their own, because they span
several documents or collections. Every check is one query or pipeline that
returns the OFFENDING documents, so an empty result means OK. seed runs this
automatically; run it again any time after using the app.

Each pipeline has a twin with the same name in queries/05_verify_data.js.
Pipelines use $$NOW (the server's clock) instead of a Python datetime, so the
mongosh and Python versions are literally the same.
"""
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from pymongo.database import Database

from core.db import get_db
from core.schemas import VALIDATORS

Pipeline = list[dict[str, Any]]
IST = ZoneInfo("Asia/Kolkata")


def _ist(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%d %b %H:%M IST")

# 1. events: each tier's `sold` equals its confirmed tickets and is <= capacity.
TIER_STOCK_PIPELINE: Pipeline = [
    {"$match": {"ticketTiers": {"$exists": True}}},
    {"$unwind": "$ticketTiers"},
    # localField/foreignField + pipeline (MongoDB 5.0+): join on eventId, then
    # filter to this tier's confirmed tickets inside the sub-pipeline.
    {"$lookup": {
        "from": "bookings",
        "localField": "_id",
        "foreignField": "eventId",
        "let": {"tierId": "$ticketTiers.tierId"},
        "pipeline": [
            {"$match": {"status": "confirmed"}},
            {"$unwind": "$tickets"},
            {"$match": {"$expr": {"$eq": ["$tickets.tierId", "$$tierId"]}}},
            {"$count": "n"},
        ],
        "as": "confirmed",
    }},
    {"$project": {
        "title": 1,
        "tierId": "$ticketTiers.tierId",
        "sold": "$ticketTiers.sold",
        "capacity": "$ticketTiers.capacity",
        "confirmedTickets": {"$ifNull": [{"$first": "$confirmed.n"}, 0]},
    }},
    {"$match": {"$expr": {"$or": [
        {"$ne": ["$sold", "$confirmedTickets"]},
        {"$gt": ["$sold", "$capacity"]},
    ]}}},
]

# 2. events: each workshop's seatsTaken equals its confirmed bookings and is <= seatLimit.
WORKSHOP_SEATS_PIPELINE: Pipeline = [
    {"$match": {"type": "workshop"}},
    {"$lookup": {
        "from": "bookings",
        "localField": "_id",
        "foreignField": "eventId",
        "pipeline": [{"$match": {"status": "confirmed"}}, {"$count": "n"}],
        "as": "confirmed",
    }},
    {"$project": {
        "title": 1,
        "seatsTaken": 1,
        "seatLimit": 1,
        "confirmedSeats": {"$ifNull": [{"$first": "$confirmed.n"}, 0]},
    }},
    {"$match": {"$expr": {"$or": [
        {"$ne": ["$seatsTaken", "$confirmedSeats"]},
        {"$gt": ["$seatsTaken", "$seatLimit"]},
    ]}}},
]

# 3. events: the venue exists and every tier fits inside its venue section (cross-collection).
TIER_SECTION_PIPELINE: Pipeline = [
    {"$lookup": {
        "from": "venues",
        "localField": "venueId",
        "foreignField": "_id",
        "pipeline": [{"$project": {"sections": 1}}],
        "as": "venue",
    }},
    {"$set": {"venue": {"$first": "$venue"}}},
    {"$unwind": {"path": "$ticketTiers", "preserveNullAndEmptyArrays": True}},
    {"$set": {"sectionCapacity": {"$first": {"$map": {
        "input": {"$filter": {
            "input": {"$ifNull": ["$venue.sections", []]},
            "as": "s",
            "cond": {"$eq": ["$$s.name", "$ticketTiers.section"]},
        }},
        "as": "s",
        "in": "$$s.capacity",
    }}}}},
    {"$match": {"$expr": {"$or": [
        {"$eq": [{"$type": "$venue"}, "missing"]},
        {"$and": [
            {"$ne": [{"$type": "$ticketTiers"}, "missing"]},
            {"$or": [
                {"$eq": [{"$type": "$sectionCapacity"}, "missing"]},
                {"$gt": ["$ticketTiers.capacity", "$sectionCapacity"]},
            ]},
        ]},
    ]}}},
    {"$project": {"title": 1, "tierId": "$ticketTiers.tierId", "section": "$ticketTiers.section",
                  "capacity": "$ticketTiers.capacity", "sectionCapacity": 1}},
]

# 4. events: no two non-cancelled events overlap in the same venue (cross-document).
# Sorted by start time per venue, any overlap shows up between NEIGHBOURS, so
# comparing each event with the previous one ($shift by -1) is enough.
VENUE_OVERLAP_PIPELINE: Pipeline = [
    {"$match": {"status": {"$ne": "cancelled"}}},
    {"$setWindowFields": {
        "partitionBy": "$venueId",
        "sortBy": {"startTime": 1},
        "output": {
            "previousEnd": {"$shift": {"output": "$endTime", "by": -1}},
            "previousTitle": {"$shift": {"output": "$title", "by": -1}},
        },
    }},
    {"$match": {"$expr": {"$gt": ["$previousEnd", "$startTime"]}}},
    {"$project": {"title": 1, "previousTitle": 1, "startTime": 1, "previousEnd": 1}},
]

# 5. bookings: references and cross-document consistency, one reason per bad booking.
BOOKING_CONSISTENCY_PIPELINE: Pipeline = [
    {"$lookup": {
        "from": "events",
        "localField": "eventId",
        "foreignField": "_id",
        "pipeline": [{"$project": {"type": 1, "status": 1, "startTime": 1}}],
        "as": "event",
    }},
    {"$lookup": {
        "from": "users",
        "localField": "userId",
        "foreignField": "_id",
        "pipeline": [{"$project": {"_id": 1}}],
        "as": "user",
    }},
    {"$set": {"event": {"$first": "$event"}}},
    {"$project": {"problem": {"$switch": {
        "branches": [
            {"case": {"$eq": [{"$type": "$event"}, "missing"]}, "then": "event does not exist"},
            {"case": {"$eq": [{"$size": "$user"}, 0]}, "then": "user does not exist"},
            {"case": {"$ne": ["$eventType", "$event.type"]}, "then": "eventType differs from the event's type"},
            {"case": {"$and": [{"$eq": ["$status", "confirmed"]}, {"$eq": ["$event.status", "cancelled"]}]},
             "then": "confirmed booking for a cancelled event"},
            {"case": {"$and": [
                {"$ne": ["$status", "confirmed"]},
                {"$anyElementTrue": [{"$map": {"input": "$tickets", "as": "t",
                                               "in": {"$ne": ["$$t.checkedInAt", None]}}}]},
            ]}, "then": "cancelled booking has a checked-in ticket"},
            {"case": {"$gt": ["$bookedAt", "$$NOW"]}, "then": "bookedAt is in the future"},
            # Guard against null first: in BSON ordering null sorts below every date,
            # so a bare {$lt: [null, date]} would be true for every confirmed booking.
            {"case": {"$and": [{"$ne": ["$cancelledAt", None]}, {"$lt": ["$cancelledAt", "$bookedAt"]}]},
             "then": "cancelled before it was booked"},
            {"case": {"$and": [{"$ne": ["$cancelledAt", None]}, {"$gt": ["$cancelledAt", "$event.startTime"]}]},
             "then": "cancelled after the event started"},
            {"case": {"$ne": [{"$eq": ["$totalAmount", 0]}, {"$eq": ["$paymentMode", "FREE"]}]},
             "then": "paymentMode FREE must match totalAmount 0"},
        ],
        "default": None,
    }}}},
    {"$match": {"problem": {"$ne": None}}},
]

# 6. users: every shift belongs to an existing event and lies inside its time window.
SHIFT_WINDOW_PIPELINE: Pipeline = [
    {"$match": {"role": "volunteer"}},
    {"$unwind": "$shifts"},
    {"$lookup": {
        "from": "events",
        "localField": "shifts.eventId",
        "foreignField": "_id",
        "pipeline": [{"$project": {"title": 1, "startTime": 1, "endTime": 1}}],
        "as": "event",
    }},
    {"$set": {"event": {"$first": "$event"}}},
    {"$match": {"$expr": {"$or": [
        {"$eq": [{"$type": "$event"}, "missing"]},
        {"$lt": ["$shifts.start", "$event.startTime"]},
        {"$gt": ["$shifts.end", "$event.endTime"]},
    ]}}},
    {"$project": {"email": 1, "eventTitle": "$event.title", "start": "$shifts.start", "end": "$shifts.end"}},
]

# 7. users: a volunteer's shifts never overlap (same neighbour trick as check 4).
SHIFT_OVERLAP_PIPELINE: Pipeline = [
    {"$match": {"role": "volunteer"}},
    {"$unwind": "$shifts"},
    {"$setWindowFields": {
        "partitionBy": "$_id",
        "sortBy": {"shifts.start": 1},
        "output": {"previousEnd": {"$shift": {"output": "$shifts.end", "by": -1}}},
    }},
    {"$match": {"$expr": {"$gt": ["$previousEnd", "$shifts.start"]}}},
    {"$project": {"email": 1, "start": "$shifts.start", "previousEnd": 1}},
]


# --- Checks --------------------------------------------------------------------
def documents_failing_validators(db: Database) -> list[str]:
    """{$nor: [validator]} matches documents that do NOT satisfy the validator.

    Needed because collMod does not re-check existing documents, and a write with
    bypassDocumentValidation would skip the validator entirely.
    """
    problems = []
    for name, validator in VALIDATORS.items():
        count = db[name].count_documents({"$nor": [validator]})
        if count:
            problems.append(f"{name}: {count} document(s) fail the validator")
    return problems


def _pipeline_check(collection: str, pipeline: Pipeline, describe: Callable[[dict[str, Any]], str]
                    ) -> Callable[[Database], list[str]]:
    def run(db: Database) -> list[str]:
        return [describe(doc) for doc in db[collection].aggregate(pipeline)]
    return run


CHECKS: list[tuple[str, Callable[[Database], list[str]]]] = [
    ("all documents pass their validators", documents_failing_validators),
    ("tier sold == confirmed tickets <= capacity", _pipeline_check(
        "events", TIER_STOCK_PIPELINE,
        lambda d: f"{d['title']} [{d['tierId']}]: sold {d['sold']}, confirmed tickets "
                  f"{d['confirmedTickets']}, capacity {d['capacity']}")),
    ("workshop seatsTaken == confirmed bookings <= seatLimit", _pipeline_check(
        "events", WORKSHOP_SEATS_PIPELINE,
        lambda d: f"{d['title']}: seatsTaken {d['seatsTaken']}, confirmed {d['confirmedSeats']}, "
                  f"limit {d['seatLimit']}")),
    ("venue exists and each tier fits its section", _pipeline_check(
        "events", TIER_SECTION_PIPELINE,
        lambda d: f"{d['title']} [{d.get('tierId')}]: section {d.get('section')!r} holds "
                  f"{d.get('sectionCapacity', 'nothing')}, tier capacity {d.get('capacity')}")),
    ("no overlapping events in a venue", _pipeline_check(
        "events", VENUE_OVERLAP_PIPELINE,
        lambda d: f"{d['title']} starts before {d['previousTitle']} ends")),
    ("bookings reference real users/events and are consistent", _pipeline_check(
        "bookings", BOOKING_CONSISTENCY_PIPELINE,
        lambda d: f"booking {d['_id']}: {d['problem']}")),
    ("volunteer shifts lie inside their event", _pipeline_check(
        "users", SHIFT_WINDOW_PIPELINE,
        lambda d: f"{d['email']}: shift {_ist(d['start'])} outside {d.get('eventTitle', 'a missing event')}")),
    ("volunteer shifts do not overlap", _pipeline_check(
        "users", SHIFT_OVERLAP_PIPELINE,
        lambda d: f"{d['email']}: shift at {_ist(d['start'])} overlaps the previous one")),
]


def run_checks(db: Database) -> list[tuple[str, list[str]]]:
    """Run every check; returns (check name, problems) pairs. Used by tests too."""
    return [(name, check(db)) for name, check in CHECKS]


class Command(BaseCommand):
    help = "Verify cross-document invariants (stock counters, references, overlaps)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--db", default=settings.MONGO_DB_NAME, help="Database name.")

    def handle(self, *args: Any, **options: Any) -> None:
        db = get_db(options["db"])
        self.stdout.write(f"Verifying '{db.name}'")
        failed = 0
        for name, problems in run_checks(db):
            if not problems:
                self.stdout.write(self.style.SUCCESS(f"  OK    {name}"))
                continue
            failed += 1
            self.stdout.write(self.style.ERROR(f"  FAIL  {name} ({len(problems)} problem(s))"))
            for problem in problems[:10]:
                self.stdout.write(f"          {problem}")
        if failed:
            raise CommandError(f"{failed} check(s) failed.")
        self.stdout.write(self.style.SUCCESS("All invariants hold."))
