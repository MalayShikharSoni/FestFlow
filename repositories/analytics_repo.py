"""
Every aggregation pipeline, one function each.

For each pipeline there are two functions:
  * <name>_pipeline(...) builds the stage list (so the Query Lab can show it), and
  * <name>(...) runs it and returns plain dicts (ObjectIds turned into strings).
queries/03_aggregations.js contains the identical pipelines under the same names.

Times: all dates are stored in UTC. Wherever a pipeline groups by hour or day
it passes timezone "Asia/Kolkata", so "a day" means an IST calendar day.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from bson import ObjectId
from pymongo.errors import OperationFailure

from core.db import get_db
from core.exceptions import QueryFailed

Pipeline = list[dict[str, Any]]
IST = "Asia/Kolkata"
MAX_TIME_MS = 5000  # the server aborts a pipeline that runs longer than this


# --- Running -------------------------------------------------------------------
def _plain(value: Any) -> Any:
    """Recursively turn ObjectIds into strings so results go straight to templates/JSON."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def run_pipeline(collection: str, pipeline: Pipeline, limit: int | None = None) -> list[dict[str, Any]]:
    """Run any pipeline with a server-side time limit; optionally stop reading after `limit` rows."""
    cursor = get_db()[collection].aggregate(pipeline, maxTimeMS=MAX_TIME_MS)
    rows = []
    for doc in cursor:
        if limit is not None and len(rows) >= limit:
            break
        rows.append(_plain(doc))
    cursor.close()
    return rows


def run_untrusted_pipeline(collection: str, pipeline: Pipeline, limit: int) -> list[dict[str, Any]]:
    """Run a Query Lab pipeline typed by an organizer (already checked by analytics_service).

    A {$limit: limit + 1} is appended so the server stops early and we can tell
    whether the result was cut off. Server errors (bad operator, timeout after
    maxTimeMS, ...) become QueryFailed carrying MongoDB's message verbatim.
    Results are NOT passed through _plain(): the lab shows raw Extended JSON.
    """
    try:
        cursor = get_db()[collection].aggregate(pipeline + [{"$limit": limit + 1}], maxTimeMS=MAX_TIME_MS)
        return list(cursor)
    except OperationFailure as exc:
        message = (exc.details or {}).get("errmsg", str(exc))
        code_name = (exc.details or {}).get("codeName", "")
        raise QueryFailed(f"MongoDB error {exc.code} {code_name}: {message}") from exc


def _confirmed_tickets() -> dict[str, Any]:
    return {"$size": "$tickets"}


# --- 1. revenue_by_event_and_tier ----------------------------------------------
def revenue_by_event_and_tier_pipeline() -> Pipeline:
    """Question: how much money did each tier of each event bring in?

    $match    only confirmed bookings count as revenue (refunds are excluded)
    $unwind   one document per ticket, because price lives on the ticket
    $group    by (event, tier): total price and ticket count
    $lookup   pipeline form, fetching ONLY title and type of the event (small join)
    $unwind   the lookup returns an array of 1; flatten it
    $project  clean output shape
    $sort     biggest earners first (title/tier break ties so the order is stable)
    """
    return [
        {"$match": {"status": "confirmed"}},
        {"$unwind": "$tickets"},
        {"$group": {
            "_id": {"eventId": "$eventId", "tierId": "$tickets.tierId"},
            "revenue": {"$sum": "$tickets.price"},
            "tickets": {"$sum": 1},
        }},
        {"$lookup": {
            "from": "events",
            "localField": "_id.eventId",
            "foreignField": "_id",
            "pipeline": [{"$project": {"_id": 0, "title": 1, "type": 1}}],
            "as": "event",
        }},
        {"$unwind": "$event"},
        {"$project": {
            "_id": 0, "eventId": "$_id.eventId", "tierId": "$_id.tierId",
            "title": "$event.title", "type": "$event.type", "revenue": 1, "tickets": 1,
        }},
        {"$sort": {"revenue": -1, "title": 1, "tierId": 1}},
    ]


def revenue_by_event_and_tier() -> list[dict[str, Any]]:
    return run_pipeline("bookings", revenue_by_event_and_tier_pipeline())


# --- 2. occupancy_per_tier -----------------------------------------------------
def occupancy_per_tier_pipeline() -> Pipeline:
    """Question: how full is every ticket tier, and how big is the section it sells?

    $match    ticketed, non-cancelled events (workshops have no tiers)
    $unwind   one document per tier
    $lookup   venues with `let` + pipeline: find the venue, $unwind its sections and
              keep only the section this tier sells ($expr compares with $$section)
    $unwind   flatten the single matched section
    $project  occupancyPct = sold / capacity * 100, $round to 1 decimal
    $sort     fullest tiers first
    """
    return [
        {"$match": {"type": {"$in": ["pro_show", "cultural"]}, "status": {"$ne": "cancelled"}}},
        {"$unwind": "$ticketTiers"},
        {"$lookup": {
            "from": "venues",
            "let": {"venueId": "$venueId", "section": "$ticketTiers.section"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$_id", "$$venueId"]}}},
                {"$unwind": "$sections"},
                {"$match": {"$expr": {"$eq": ["$sections.name", "$$section"]}}},
                {"$project": {"_id": 0, "venue": "$name", "sectionCapacity": "$sections.capacity"}},
            ],
            "as": "venue",
        }},
        {"$unwind": "$venue"},
        {"$project": {
            "_id": 0, "eventId": "$_id", "title": 1,
            "tierId": "$ticketTiers.tierId", "tierName": "$ticketTiers.name",
            "venue": "$venue.venue", "section": "$ticketTiers.section",
            "sold": "$ticketTiers.sold", "capacity": "$ticketTiers.capacity",
            "sectionCapacity": "$venue.sectionCapacity",
            "occupancyPct": {"$round": [
                {"$multiply": [{"$divide": ["$ticketTiers.sold", "$ticketTiers.capacity"]}, 100]}, 1]},
        }},
        {"$sort": {"occupancyPct": -1, "title": 1, "tierId": 1}},
    ]


def occupancy_per_tier() -> list[dict[str, Any]]:
    return run_pipeline("events", occupancy_per_tier_pipeline())


# --- 3. checkin_traffic_by_hour ------------------------------------------------
def checkin_traffic_by_hour_pipeline(event_id: ObjectId | None = None) -> Pipeline:
    """Question: how many people came through the gates in each hour (IST)?

    $match    (first, so it can use an index) bookings that have at least one
              check-in, optionally for one event only. $elemMatch means "some
              ticket has checkedInAt != null". The tempting
              {"tickets.checkedInAt": {"$ne": null}} is WRONG on an array: it means
              "NO ticket is null", silently dropping groups where one person
              did not turn up.
    $unwind   one document per ticket
    $match    keep only tickets that were actually scanned
    $group    by $dateTrunc to the hour IN IST — without the timezone, hours
              would be cut at UTC boundaries (IST is UTC+5:30, so they would be off)
    $sort     chronological
    $project  readable label
    """
    first_match: dict[str, Any] = {"tickets": {"$elemMatch": {"checkedInAt": {"$ne": None}}}}
    if event_id is not None:
        first_match["eventId"] = event_id
    return [
        {"$match": first_match},
        {"$unwind": "$tickets"},
        {"$match": {"tickets.checkedInAt": {"$ne": None}}},
        {"$group": {
            "_id": {"$dateTrunc": {"date": "$tickets.checkedInAt", "unit": "hour", "timezone": IST}},
            "checkIns": {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
        {"$project": {
            "_id": 0, "hour": "$_id", "checkIns": 1,
            "label": {"$dateToString": {"date": "$_id", "format": "%d-%m %H:00", "timezone": IST}},
        }},
    ]


def checkin_traffic_by_hour(event_id: ObjectId | None = None) -> list[dict[str, Any]]:
    return run_pipeline("bookings", checkin_traffic_by_hour_pipeline(event_id))


# --- 4. no_show_rate_per_event -------------------------------------------------
def no_show_rate_per_event_pipeline() -> Pipeline:
    """Question: of the tickets sold for finished events, how many were never scanned?

    $match    confirmed bookings (cancelled ones were never expected to attend)
    $unwind   one document per ticket
    $group    per event: tickets sold, and checked in via $cond (1 if scanned, else 0)
    $lookup   the event's title/type/status
    $unwind   flatten it
    $match    only completed events — future events have not had their chance yet
    $project  noShowPct = (sold - checkedIn) / sold * 100
    $sort     worst no-show rate first

    Why $group BEFORE $lookup: joining first would run one lookup per booking
    (~4,500); grouping first leaves one row per event (~25) to join. Same result,
    measured ~800 ms -> ~15 ms on the 5,000-booking seed.
    """
    return [
        {"$match": {"status": "confirmed"}},
        {"$unwind": "$tickets"},
        {"$group": {
            "_id": "$eventId",
            "sold": {"$sum": 1},
            "checkedIn": {"$sum": {"$cond": [{"$ne": ["$tickets.checkedInAt", None]}, 1, 0]}},
        }},
        {"$lookup": {
            "from": "events",
            "localField": "_id",
            "foreignField": "_id",
            "pipeline": [{"$project": {"_id": 0, "title": 1, "type": 1, "status": 1}}],
            "as": "event",
        }},
        {"$unwind": "$event"},
        {"$match": {"event.status": "completed"}},
        {"$project": {
            "_id": 0, "eventId": "$_id", "title": "$event.title", "type": "$event.type",
            "sold": 1, "checkedIn": 1,
            "noShows": {"$subtract": ["$sold", "$checkedIn"]},
            "noShowPct": {"$round": [{"$multiply": [
                {"$divide": [{"$subtract": ["$sold", "$checkedIn"]}, "$sold"]}, 100]}, 1]},
        }},
        {"$sort": {"noShowPct": -1, "title": 1}},
    ]


def no_show_rate_per_event() -> list[dict[str, Any]]:
    return run_pipeline("bookings", no_show_rate_per_event_pipeline())


# --- 5. booking_lead_time_buckets ----------------------------------------------
LEAD_TIME_LABELS = {0: "same day", 1: "1-2 days", 3: "3-6 days", 7: "1-2 weeks", 14: "2-4 weeks", "30+": "30+ days"}


def booking_lead_time_buckets_pipeline() -> Pipeline:
    """Question: how far in advance do people book?

    Runs on `events` and joins the bookings, not the other way round:
    $project  each event's startTime (all we need from it)
    $lookup   its bookings via the {eventId, status} index, keeping only bookedAt
              and the ticket count — 25 indexed lookups instead of one lookup per
              booking (5,000+); same result, measured ~760 ms -> ~75 ms
    $unwind   one document per booking
    $set      leadDays via $dateDiff in days ($dateDiff counts IST midnights
              crossed, not 24-hour periods: 23:00 -> 01:00 next day is 1 day)
    $bucket   boundaries [0,1,3,7,14,30] -> buckets 0, 1-2, 3-6, 7-13, 14-29;
              anything >= 30 falls into the default bucket "30+"
    $project  rounded average tickets per booking
    """
    return [
        {"$project": {"startTime": 1}},
        {"$lookup": {
            "from": "bookings",
            "localField": "_id",
            "foreignField": "eventId",
            "pipeline": [{"$project": {"_id": 0, "bookedAt": 1, "ticketCount": {"$size": "$tickets"}}}],
            "as": "booking",
        }},
        {"$unwind": "$booking"},
        {"$set": {
            "leadDays": {"$dateDiff": {"startDate": "$booking.bookedAt", "endDate": "$startTime",
                                       "unit": "day", "timezone": IST}},
            "ticketCount": "$booking.ticketCount",
        }},
        {"$bucket": {
            "groupBy": "$leadDays",
            "boundaries": [0, 1, 3, 7, 14, 30],
            "default": "30+",
            "output": {"bookings": {"$sum": 1}, "avgTickets": {"$avg": "$ticketCount"}},
        }},
        {"$project": {"_id": 0, "bucket": "$_id", "bookings": 1, "avgTickets": {"$round": ["$avgTickets", 2]}}},
    ]


def booking_lead_time_buckets() -> list[dict[str, Any]]:
    rows = run_pipeline("events", booking_lead_time_buckets_pipeline())
    for row in rows:
        row["label"] = LEAD_TIME_LABELS.get(row["bucket"], str(row["bucket"]))
    return rows


# --- 6. top_spenders -----------------------------------------------------------
def top_spenders_pipeline(limit: int = 10) -> Pipeline:
    """Question: who are our ten biggest customers?

    $match           confirmed bookings only
    $group           total spent and number of bookings per user
    $setWindowFields $rank over everyone, by total spent (ties share a rank,
                     the next rank is skipped: 1, 2, 2, 4)
    $sort            by rank, user id breaks ties so the order is stable
    $limit           top N — BEFORE the $lookup, so we only join N users
    $lookup          name and college of those N users
    $unwind/$project clean output
    """
    return [
        {"$match": {"status": "confirmed"}},
        {"$group": {"_id": "$userId", "totalSpent": {"$sum": "$totalAmount"}, "bookings": {"$sum": 1}}},
        {"$setWindowFields": {"sortBy": {"totalSpent": -1}, "output": {"rank": {"$rank": {}}}}},
        {"$sort": {"rank": 1, "_id": 1}},
        {"$limit": limit},
        {"$lookup": {
            "from": "users",
            "localField": "_id",
            "foreignField": "_id",
            "pipeline": [{"$project": {"_id": 0, "name": 1, "college": 1}}],
            "as": "user",
        }},
        {"$unwind": "$user"},
        {"$project": {
            "_id": 0, "rank": 1, "userId": "$_id", "name": "$user.name", "college": "$user.college",
            "totalSpent": 1, "bookings": 1,
        }},
    ]


def top_spenders(limit: int = 10) -> list[dict[str, Any]]:
    return run_pipeline("bookings", top_spenders_pipeline(limit))


# --- 7. revenue_by_payment_mode ------------------------------------------------
def revenue_by_payment_mode_pipeline() -> Pipeline:
    """Question: how do people pay, and what share of revenue does each mode bring?

    $match           confirmed bookings
    $group           revenue and bookings per payment mode
    $setWindowFields an UNBOUNDED window (whole result set) sums all revenue onto
                     every row, so each row can compute its own share — no second query
    $project         sharePct = revenue / totalRevenue * 100
    $sort            biggest first
    """
    return [
        {"$match": {"status": "confirmed"}},
        {"$group": {"_id": "$paymentMode", "revenue": {"$sum": "$totalAmount"}, "bookings": {"$sum": 1}}},
        {"$setWindowFields": {"output": {
            "totalRevenue": {"$sum": "$revenue", "window": {"documents": ["unbounded", "unbounded"]}},
        }}},
        {"$project": {
            "_id": 0, "mode": "$_id", "revenue": 1, "bookings": 1,
            "sharePct": {"$cond": [
                {"$gt": ["$totalRevenue", 0]},
                {"$round": [{"$multiply": [{"$divide": ["$revenue", "$totalRevenue"]}, 100]}, 1]},
                0,
            ]},
        }},
        {"$sort": {"revenue": -1, "mode": 1}},
    ]


def revenue_by_payment_mode() -> list[dict[str, Any]]:
    return run_pipeline("bookings", revenue_by_payment_mode_pipeline())


# --- 8. volunteer_hours + coverage_per_event -----------------------------------
def volunteer_hours_pipeline() -> Pipeline:
    """Question: how many hours has each volunteer been rostered for?

    $match    volunteers (served by the {role: 1} index)
    $unwind   one document per embedded shift
    $set      shift length in minutes with $dateDiff
    $group    per volunteer: number of shifts and total minutes
    $project  hours, rounded to 1 decimal
    $sort     most hours first
    """
    return [
        {"$match": {"role": "volunteer"}},
        {"$unwind": "$shifts"},
        {"$set": {"minutes": {"$dateDiff": {"startDate": "$shifts.start", "endDate": "$shifts.end", "unit": "minute"}}}},
        {"$group": {
            "_id": "$_id", "name": {"$first": "$name"}, "email": {"$first": "$email"},
            "shifts": {"$sum": 1}, "minutes": {"$sum": "$minutes"},
        }},
        {"$project": {"_id": 0, "userId": "$_id", "name": 1, "email": 1, "shifts": 1,
                      "hours": {"$round": [{"$divide": ["$minutes", 60]}, 1]}}},
        {"$sort": {"hours": -1, "name": 1}},
    ]


def volunteer_hours() -> list[dict[str, Any]]:
    return run_pipeline("users", volunteer_hours_pipeline())


def coverage_per_event_pipeline() -> Pipeline:
    """Question: how many volunteers cover each duty at each event?

    $match/$unwind  volunteers' shifts, one per document
    $group          count per (event, duty)
    $group          again per event: total, plus an object {gate: 2, stage: 1, ...}
                    built with $arrayToObject so a table can show duties as columns
    $lookup         event title and start time
    $sort           in schedule order
    """
    return [
        {"$match": {"role": "volunteer"}},
        {"$unwind": "$shifts"},
        {"$group": {"_id": {"eventId": "$shifts.eventId", "duty": "$shifts.duty"}, "volunteers": {"$sum": 1}}},
        {"$group": {
            "_id": "$_id.eventId",
            "total": {"$sum": "$volunteers"},
            "duties": {"$push": {"k": "$_id.duty", "v": "$volunteers"}},
        }},
        {"$lookup": {
            "from": "events",
            "localField": "_id",
            "foreignField": "_id",
            "pipeline": [{"$project": {"_id": 0, "title": 1, "startTime": 1}}],
            "as": "event",
        }},
        {"$unwind": "$event"},
        {"$project": {
            "_id": 0, "eventId": "$_id", "title": "$event.title", "startTime": "$event.startTime",
            "total": 1, "byDuty": {"$arrayToObject": "$duties"},
        }},
        {"$sort": {"startTime": 1}},
    ]


def coverage_per_event() -> list[dict[str, Any]]:
    return run_pipeline("users", coverage_per_event_pipeline())


# --- 9. cancellation_rate_by_event_type ----------------------------------------
def cancellation_rate_by_event_type_pipeline() -> Pipeline:
    """Question: which kind of event do people cancel most?

    Uses the DENORMALIZED bookings.eventType — no $lookup into events needed.
    $group    per event type: all bookings, plus cancelled and refunded counted
              with conditional sums ($cond inside $sum)
    $project  cancellationPct = (cancelled + refunded) / bookings * 100
    $sort     highest rate first
    """
    def count_status(status: str) -> dict[str, Any]:
        return {"$sum": {"$cond": [{"$eq": ["$status", status]}, 1, 0]}}

    return [
        {"$group": {"_id": "$eventType", "bookings": {"$sum": 1},
                    "cancelled": count_status("cancelled"), "refunded": count_status("refunded")}},
        {"$project": {
            "_id": 0, "eventType": "$_id", "bookings": 1, "cancelled": 1, "refunded": 1,
            "cancellationPct": {"$round": [{"$multiply": [
                {"$divide": [{"$add": ["$cancelled", "$refunded"]}, "$bookings"]}, 100]}, 1]},
        }},
        {"$sort": {"cancellationPct": -1, "eventType": 1}},
    ]


def cancellation_rate_by_event_type() -> list[dict[str, Any]]:
    return run_pipeline("bookings", cancellation_rate_by_event_type_pipeline())


# --- 10. daily_sales_with_moving_avg -------------------------------------------
def daily_sales_with_moving_avg_pipeline() -> Pipeline:
    """Question: how many tickets sold per day, and what is the 3-day trend?

    $match           confirmed bookings
    $group           tickets and revenue per IST day ($dateTrunc unit "day")
    $setWindowFields $avg over a RANGE window of [-2, 0] days: today and the two
                     calendar days before. A range window is based on the dates
                     themselves, so a day with no sales is not silently skipped
                     the way a `documents: [-2, 0]` window (previous 2 rows) would.
    $project/$sort   rounded, chronological
    """
    return [
        {"$match": {"status": "confirmed"}},
        {"$group": {
            "_id": {"$dateTrunc": {"date": "$bookedAt", "unit": "day", "timezone": IST}},
            "tickets": {"$sum": _confirmed_tickets()},
            "revenue": {"$sum": "$totalAmount"},
        }},
        {"$setWindowFields": {
            "sortBy": {"_id": 1},
            "output": {"movingAvg3d": {"$avg": "$tickets", "window": {"range": [-2, 0], "unit": "day"}}},
        }},
        {"$project": {"_id": 0, "day": "$_id", "tickets": 1, "revenue": 1,
                      "movingAvg3d": {"$round": ["$movingAvg3d", 1]}}},
        {"$sort": {"day": 1}},
    ]


def daily_sales_with_moving_avg() -> list[dict[str, Any]]:
    return run_pipeline("bookings", daily_sales_with_moving_avg_pipeline())


# --- 11. organizer_dashboard ---------------------------------------------------
def organizer_dashboard_pipeline() -> Pipeline:
    """Question: everything the dashboard cards need — in ONE round trip.

    $facet runs several sub-pipelines over the same input documents and returns
    one document with one array per facet:
      kpis          totals: bookings, tickets, revenue, check-ins, cancellation %
      salesByDay    tickets per IST day
      topEvents     5 highest-earning events (with a $lookup for the title)
      paymentModes  bookings and revenue per mode
    The final $project turns the one-element kpis array into a plain object.
    """
    confirmed = {"$eq": ["$status", "confirmed"]}
    return [
        {"$facet": {
            "kpis": [
                {"$group": {
                    "_id": None,
                    "bookings": {"$sum": 1},
                    "confirmed": {"$sum": {"$cond": [confirmed, 1, 0]}},
                    "ticketsSold": {"$sum": {"$cond": [confirmed, _confirmed_tickets(), 0]}},
                    "revenue": {"$sum": {"$cond": [confirmed, "$totalAmount", 0]}},
                    "checkedIn": {"$sum": {"$size": {"$filter": {
                        "input": "$tickets", "cond": {"$ne": ["$$this.checkedInAt", None]}}}}},
                }},
                {"$project": {
                    "_id": 0, "bookings": 1, "confirmed": 1, "ticketsSold": 1, "revenue": 1, "checkedIn": 1,
                    "cancellationPct": {"$round": [{"$multiply": [
                        {"$divide": [{"$subtract": ["$bookings", "$confirmed"]}, "$bookings"]}, 100]}, 1]},
                }},
            ],
            "salesByDay": [
                {"$match": {"status": "confirmed"}},
                {"$group": {"_id": {"$dateTrunc": {"date": "$bookedAt", "unit": "day", "timezone": IST}},
                            "tickets": {"$sum": _confirmed_tickets()}}},
                {"$sort": {"_id": 1}},
                {"$project": {"_id": 0, "day": "$_id", "tickets": 1}},
            ],
            "topEvents": [
                {"$match": {"status": "confirmed"}},
                {"$group": {"_id": "$eventId", "revenue": {"$sum": "$totalAmount"},
                            "tickets": {"$sum": _confirmed_tickets()}}},
                {"$sort": {"revenue": -1, "_id": 1}},
                {"$limit": 5},
                {"$lookup": {"from": "events", "localField": "_id", "foreignField": "_id",
                             "pipeline": [{"$project": {"_id": 0, "title": 1}}], "as": "event"}},
                {"$unwind": "$event"},
                {"$project": {"_id": 0, "eventId": "$_id", "title": "$event.title", "revenue": 1, "tickets": 1}},
            ],
            "paymentModes": [
                {"$match": {"status": "confirmed"}},
                {"$group": {"_id": "$paymentMode", "bookings": {"$sum": 1}, "revenue": {"$sum": "$totalAmount"}}},
                {"$sort": {"bookings": -1, "_id": 1}},
                {"$project": {"_id": 0, "mode": "$_id", "bookings": 1, "revenue": 1}},
            ],
        }},
        {"$project": {"kpis": {"$first": "$kpis"}, "salesByDay": 1, "topEvents": 1, "paymentModes": 1}},
    ]


def organizer_dashboard() -> dict[str, Any]:
    row = run_pipeline("bookings", organizer_dashboard_pipeline())[0]
    # With no bookings, the kpis facet is empty and $first of [] is "missing", so the key is absent.
    return {"kpis": row.get("kpis"), "salesByDay": row["salesByDay"], "topEvents": row["topEvents"],
            "paymentModes": row["paymentModes"]}


# --- Registry for the Query Lab ------------------------------------------------
@dataclass(frozen=True)
class NamedPipeline:
    name: str
    collection: str
    question: str
    build: Callable[[], Pipeline]


NAMED_PIPELINES: dict[str, NamedPipeline] = {p.name: p for p in [
    NamedPipeline("revenue_by_event_and_tier", "bookings", "Revenue per event and tier",
                  revenue_by_event_and_tier_pipeline),
    NamedPipeline("occupancy_per_tier", "events", "How full is every tier?", occupancy_per_tier_pipeline),
    NamedPipeline("checkin_traffic_by_hour", "bookings", "Gate check-ins per hour (IST)",
                  checkin_traffic_by_hour_pipeline),
    NamedPipeline("no_show_rate_per_event", "bookings", "No-show rate of completed events",
                  no_show_rate_per_event_pipeline),
    NamedPipeline("booking_lead_time_buckets", "events", "How far ahead do people book?",
                  booking_lead_time_buckets_pipeline),
    NamedPipeline("top_spenders", "bookings", "Top 10 customers by spend", top_spenders_pipeline),
    NamedPipeline("revenue_by_payment_mode", "bookings", "Revenue share per payment mode",
                  revenue_by_payment_mode_pipeline),
    NamedPipeline("volunteer_hours", "users", "Rostered hours per volunteer", volunteer_hours_pipeline),
    NamedPipeline("coverage_per_event", "users", "Volunteers per duty per event", coverage_per_event_pipeline),
    NamedPipeline("cancellation_rate_by_event_type", "bookings", "Cancellation rate by event type",
                  cancellation_rate_by_event_type_pipeline),
    NamedPipeline("daily_sales_with_moving_avg", "bookings", "Tickets per day with 3-day moving average",
                  daily_sales_with_moving_avg_pipeline),
    NamedPipeline("organizer_dashboard", "bookings", "All dashboard numbers in one $facet",
                  organizer_dashboard_pipeline),
]}


def latest_activity() -> datetime | None:
    """Most recent booking time — handy to show "data as of" on the dashboard."""
    doc = get_db().bookings.find_one({}, {"bookedAt": 1}, sort=[("bookedAt", -1)])
    return doc["bookedAt"] if doc else None
