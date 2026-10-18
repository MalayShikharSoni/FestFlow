"""
python manage.py explain_report [--db NAME] [--out docs/explain_results.md]

For each query: drop the index that serves it, run explain("executionStats"),
recreate the index from core/indexes.py, run explain again, and write a
before/after Markdown table to docs/explain_results.md. Also shows a COVERED
query (answered from the index alone: totalDocsExamined = 0).

Run it on a big dataset for a dramatic difference:
    python manage.py seed --reset --bookings 50000
    python manage.py explain_report

DEV ONLY: it drops indexes on the live database for a moment (including unique
ones, which briefly stop guarding against duplicates). queries/04_indexes_explain.js
is the mongosh twin.
"""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from pymongo.database import Database

from core.db import get_db
from core.indexes import INDEXES

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class QueryCase:
    title: str
    collection: str
    drop: list[str]                    # index names that could serve the query
    filter: dict[str, Any]
    sort: dict[str, int] | None = None
    projection: dict[str, Any] | None = None
    filter_without_index: dict[str, Any] | None = None  # if the query cannot run without its index
    note: str = ""


@dataclass
class PlanSummary:
    winning: str
    stages: str
    index: str
    n_returned: int
    keys_examined: int
    docs_examined: int
    millis: int
    extra: dict[str, Any] = field(default_factory=dict)


def explain(db: Database, case: QueryCase, filter_: dict[str, Any]) -> PlanSummary:
    """Run the find command under explain with "executionStats" verbosity.

    It runs twice and reports the second run: the first one after an index
    rebuild pays for loading the index into memory, which is not the query's cost.
    """
    command: dict[str, Any] = {"find": case.collection, "filter": filter_}
    if case.sort:
        command["sort"] = case.sort
    if case.projection:
        command["projection"] = case.projection
    db.command("explain", command, verbosity="executionStats")  # warm-up
    result = db.command("explain", command, verbosity="executionStats")
    return summarize(result)


def summarize(result: dict[str, Any]) -> PlanSummary:
    """Pull the numbers we report out of an explain() result."""
    plan = result["queryPlanner"]["winningPlan"]
    plan = plan.get("queryPlan", plan)  # newer servers nest the classic plan under queryPlan
    stages, indexes = [], []
    node = plan
    while node:
        stages.append(node["stage"])
        if node.get("indexName"):
            indexes.append(node["indexName"])
        node = node.get("inputStage") or (node.get("inputStages") or [None])[0]
    if "COLLSCAN" in stages:
        winning = "COLLSCAN"
    elif any(s.startswith("TEXT") for s in stages):
        winning = "TEXT"
    elif "IXSCAN" in stages or "EXPRESS_IXSCAN" in stages:
        winning = "IXSCAN"
    else:
        winning = stages[-1]
    stats = result["executionStats"]
    return PlanSummary(
        winning=winning,
        stages=" > ".join(stages),
        index=indexes[-1] if indexes else "-",
        n_returned=stats["nReturned"],
        keys_examined=stats["totalKeysExamined"],
        docs_examined=stats["totalDocsExamined"],
        millis=stats["executionTimeMillis"],
    )


def index_model(collection: str, name: str) -> Any:
    return next(m for m in INDEXES[collection] if m.document["name"] == name)


def before_and_after(db: Database, case: QueryCase) -> tuple[PlanSummary, PlanSummary]:
    coll = db[case.collection]
    existing = set(coll.index_information())
    try:
        for name in case.drop:
            if name in existing:
                coll.drop_index(name)
        before = explain(db, case, case.filter_without_index or case.filter)
    finally:
        # Always put the indexes back, even if explain failed.
        coll.create_indexes([index_model(case.collection, name) for name in case.drop])
    after = explain(db, case, case.filter)
    return before, after


def build_cases(db: Database) -> tuple[list[QueryCase], QueryCase]:
    event = db.events.find_one({"slug": "bollywood-night-aria-sen"}, {"_id": 1})
    attendee = db.users.find_one({"email": "attendee@spectra.test"}, {"_id": 1})
    booking = db.bookings.find_one({}, {"tickets.code": 1}, sort=[("_id", 1)])
    if not (event and attendee and booking):
        raise CommandError("Seed the database first: python manage.py seed --reset --bookings 50000")
    by_event = {"eventId": event["_id"], "status": "confirmed"}
    cases = [
        QueryCase("a) bookings of one event with status confirmed", "bookings",
                  ["bookings_eventId_status"], by_event),
        QueryCase("b) cultural events sorted by start time", "events",
                  ["events_type_startTime", "events_startTime"], {"type": "cultural"}, sort={"startTime": 1},
                  note="Both indexes are dropped for 'before': {startTime: 1} alone could also serve the sort."),
        QueryCase("c) search 'dance'", "events", ["events_text"],
                  {"$text": {"$search": "dance"}},
                  filter_without_index={"$or": [{"title": {"$regex": "dance", "$options": "i"}},
                                                {"description": {"$regex": "dance", "$options": "i"}},
                                                {"tags": {"$regex": "dance", "$options": "i"}}]},
                  note="$text cannot run at all without a text index (error 27, IndexNotFound), so "
                       "'before' is the case-insensitive regex search you would write instead."),
        QueryCase("d) my bookings, newest first", "bookings", ["bookings_userId_bookedAt"],
                  {"userId": attendee["_id"]}, sort={"bookedAt": -1}),
        QueryCase("e) gate check-in: find a ticket code", "bookings", ["bookings_ticketCode_unique"],
                  {"tickets.code": booking["tickets"][0]["code"]}),
    ]
    covered = QueryCase("covered: event + status, projecting only indexed fields", "bookings", [],
                        by_event, projection={"_id": 0, "eventId": 1, "status": 1})
    return cases, covered


def markdown(db: Database, rows: list[tuple[QueryCase, PlanSummary, PlanSummary]],
             covered: tuple[QueryCase, PlanSummary, PlanSummary]) -> str:
    generated = datetime.now(IST).strftime("%d %b %Y %H:%M IST")
    counts = {name: db[name].estimated_document_count() for name in ("bookings", "events", "users")}
    lines = [
        "# Index and explain() results",
        "",
        f"Generated by `python manage.py explain_report` on {generated} against database "
        f"`{db.name}` ({counts['bookings']:,} bookings, {counts['events']} events, {counts['users']:,} users).",
        "Each query was explained with `executionStats` after dropping its index (**before**) and "
        "again after recreating it (**after**). Times are from a second, warm run and vary by a few ms.",
        "",
        "| Query | Run | Winning | Stages | Index | nReturned | keysExamined | docsExamined | ms |",
        "|---|---|---|---|---|---:|---:|---:|---:|",
    ]
    for case, before, after in rows:
        for label, s in (("before", before), ("after", after)):
            lines.append(f"| {case.title if label == 'before' else ''} | {label} | **{s.winning}** | "
                         f"`{s.stages}` | {s.index} | {s.n_returned:,} | {s.keys_examined:,} | "
                         f"{s.docs_examined:,} | {s.millis} |")
    lines += ["", "Notes:", ""]
    lines += [f"- **{case.title}** — {case.note}" for case, _, _ in rows if case.note]
    case, plain, cov = covered
    lines += [
        "",
        "## Covered query",
        "",
        "The filter and the projection use only fields that are in the `{eventId: 1, status: 1}` index, and "
        "`_id` is excluded, so MongoDB answers from the index keys without reading a single document.",
        "",
        "| Projection | Winning | Stages | nReturned | keysExamined | docsExamined | ms |",
        "|---|---|---|---:|---:|---:|---:|",
        f"| whole document | {plain.winning} | `{plain.stages}` | {plain.n_returned:,} | {plain.keys_examined:,} | "
        f"{plain.docs_examined:,} | {plain.millis} |",
        f"| `{{_id: 0, eventId: 1, status: 1}}` | {cov.winning} | `{cov.stages}` | {cov.n_returned:,} | "
        f"{cov.keys_examined:,} | **{cov.docs_examined:,}** | {cov.millis} |",
        "",
        "## How to read this",
        "",
        "- **COLLSCAN** reads every document in the collection; **IXSCAN** walks only the matching index keys.",
        "- `docsExamined` far above `nReturned` means wasted work; with a good index they are close.",
        "- A `SORT` stage means the results were sorted in memory; with `{type: 1, startTime: 1}` the index "
        "order already is the sort order, so the stage disappears (equality field first, sort field second).",
        "- `PROJECTION_COVERED` with `docsExamined: 0` is a covered query.",
        "- `EXPRESS_IXSCAN` (MongoDB 8.0+) is a fast path for an equality match on a unique index: "
        "it skips the general query planner.",
        "",
    ]
    return "\n".join(lines)


class Command(BaseCommand):
    help = "Drop/recreate indexes around explain('executionStats') and write docs/explain_results.md."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--db", default=settings.MONGO_DB_NAME)
        parser.add_argument("--out", type=Path, default=settings.BASE_DIR / "docs" / "explain_results.md")

    def handle(self, *args: Any, **options: Any) -> None:
        db = get_db(options["db"])
        cases, covered_case = build_cases(db)
        rows = []
        for case in cases:
            before, after = before_and_after(db, case)
            rows.append((case, before, after))
            self.stdout.write(f"{case.title}\n  before: {before.winning:8} docs {before.docs_examined:>7,} "
                              f"keys {before.keys_examined:>7,} {before.millis:>4} ms\n  after:  {after.winning:8} "
                              f"docs {after.docs_examined:>7,} keys {after.keys_examined:>7,} {after.millis:>4} ms")
        plain = explain(db, QueryCase(**{**covered_case.__dict__, "projection": None}), covered_case.filter)
        cov = explain(db, covered_case, covered_case.filter)
        self.stdout.write(f"covered query: {cov.stages}, docsExamined {cov.docs_examined}")
        options["out"].parent.mkdir(parents=True, exist_ok=True)
        options["out"].write_text(markdown(db, rows, (covered_case, plain, cov)), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote {options['out']}"))
