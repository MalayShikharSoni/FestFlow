"""
Dashboard data and the Query Lab.

The pipelines themselves live in repositories/analytics_repo.py; this module
only reshapes their results for charts/tables and guards the Query Lab.

Query Lab safety (the organizer can type ANY pipeline):
  * read-only: $out and $merge (the two stages that write) are rejected, and
    every stage must be on an allowlist — nested pipelines ($lookup, $facet,
    $unionWith) are checked too;
  * no server-side JavaScript ($where, $function, $accumulator);
  * joins may only read our four collections;
  * the server kills it after maxTimeMS = 5000, and at most 200 rows come back.
"""
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from core.exceptions import NotFoundError, RuleViolation
from core.serializers import parse_ejson, to_ejson
from ml import noshow
from repositories import analytics_repo

IST = ZoneInfo("Asia/Kolkata")
COLLECTIONS = ("bookings", "events", "users", "venues")
MAX_ROWS = 200
ALLOWED_STAGES = {
    "$match", "$project", "$addFields", "$set", "$unset", "$group", "$sort", "$limit", "$skip",
    "$unwind", "$lookup", "$graphLookup", "$unionWith", "$facet", "$bucket", "$bucketAuto",
    "$count", "$sortByCount", "$replaceRoot", "$replaceWith", "$setWindowFields", "$sample",
    "$densify", "$fill", "$redact",
}
WRITE_STAGES = {"$out", "$merge"}
FORBIDDEN_OPERATORS = {"$where", "$function", "$accumulator"}  # run JavaScript on the server


# --- Dashboard ---------------------------------------------------------------
def _day_label(value: datetime) -> str:
    return value.astimezone(IST).strftime("%d %b")


def dashboard() -> dict[str, Any]:
    """Everything the organizer dashboard shows, ready for templates and Chart.js."""
    facet = analytics_repo.organizer_dashboard()

    revenue_per_event: dict[str, int] = defaultdict(int)
    for row in analytics_repo.revenue_by_event_and_tier():
        revenue_per_event[row["title"]] += row["revenue"]
    top_revenue = sorted(revenue_per_event.items(), key=lambda kv: (-kv[1], kv[0]))[:12]

    checkins = analytics_repo.checkin_traffic_by_hour()
    lead = analytics_repo.booking_lead_time_buckets()
    payments = analytics_repo.revenue_by_payment_mode()
    daily = analytics_repo.daily_sales_with_moving_avg()

    charts = {
        "revenueByEvent": {"labels": [t for t, _ in top_revenue], "values": [v for _, v in top_revenue]},
        "checkinsByHour": {"labels": [r["label"] for r in checkins], "values": [r["checkIns"] for r in checkins]},
        "leadTime": {"labels": [r["label"] for r in lead], "values": [r["bookings"] for r in lead]},
        "paymentShare": {"labels": [r["mode"] for r in payments], "values": [r["sharePct"] for r in payments]},
        "dailySales": {
            "labels": [_day_label(r["day"]) for r in daily],
            "tickets": [r["tickets"] for r in daily],
            "movingAvg": [r["movingAvg3d"] for r in daily],
        },
    }
    return {
        "kpis": facet["kpis"],
        "top_events": facet["topEvents"],
        "charts": charts,
        "occupancy": analytics_repo.occupancy_per_tier(),
        "no_shows": analytics_repo.no_show_rate_per_event(),
        "cancellations": analytics_repo.cancellation_rate_by_event_type(),
        "top_spenders": analytics_repo.top_spenders(),
        "payments": payments,
        "daily": daily,
        "checkins": checkins,
        "lead": lead,
        "revenue_rows": top_revenue,
        "as_of": analytics_repo.latest_activity(),
        # None (card hidden) until `manage.py train_noshow` has saved a model.
        "noshow_forecast": noshow.forecast_upcoming(),
    }


def volunteer_overview() -> dict[str, Any]:
    return {"hours": analytics_repo.volunteer_hours(), "coverage": analytics_repo.coverage_per_event()}


# --- Query Lab ---------------------------------------------------------------
@dataclass
class LabResult:
    collection: str
    pipeline_json: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    millis: float = 0.0
    truncated: bool = False
    name: str | None = None
    question: str | None = None


def named_pipelines() -> list[analytics_repo.NamedPipeline]:
    return list(analytics_repo.NAMED_PIPELINES.values())


def run_named(name: str) -> LabResult:
    named = analytics_repo.NAMED_PIPELINES.get(name)
    if named is None:
        raise NotFoundError("Unknown pipeline.")
    result = _run(named.collection, named.build())
    result.name, result.question = named.name, named.question
    return result


def run_custom(collection: str, text: str) -> LabResult:
    """Parse, check and run a pipeline typed into the lab. Raises RuleViolation / QueryFailed."""
    if collection not in COLLECTIONS:
        raise RuleViolation(f"Collection must be one of: {', '.join(COLLECTIONS)}.")
    try:
        pipeline = parse_ejson(text)
    except ValueError as exc:
        raise RuleViolation(str(exc)) from exc
    check_pipeline(pipeline)
    return _run(collection, pipeline)


def check_pipeline(pipeline: Any, where: str = "pipeline") -> None:
    """Raise RuleViolation unless `pipeline` is a read-only list of allowed stages."""
    if not isinstance(pipeline, list) or not pipeline:
        raise RuleViolation(f"{where} must be a non-empty JSON array of stages, e.g. [{{\"$match\": {{}}}}].")
    for i, stage in enumerate(pipeline):
        if not isinstance(stage, dict) or len(stage) != 1:
            raise RuleViolation(f"{where}[{i}] must be an object with exactly one stage name.")
        (name, spec), = stage.items()
        if name in WRITE_STAGES:
            raise RuleViolation(f"{name} writes to the database; the Query Lab is read-only.")
        if name not in ALLOWED_STAGES:
            raise RuleViolation(f"{name} is not allowed in the Query Lab.")
        _check_joins(name, spec, f"{where}[{i}]")
    _check_no_javascript(pipeline)


def _check_joins(name: str, spec: Any, where: str) -> None:
    """Joined collections must be ours; nested pipelines are checked recursively."""
    if name in ("$lookup", "$graphLookup"):
        if spec.get("from") not in COLLECTIONS:
            raise RuleViolation(f"{where}: {name} may only read {', '.join(COLLECTIONS)}.")
        if "pipeline" in spec:
            check_pipeline(spec["pipeline"], f"{where}.{name}.pipeline")
    elif name == "$unionWith":
        coll = spec if isinstance(spec, str) else spec.get("coll")
        if coll not in COLLECTIONS:
            raise RuleViolation(f"{where}: $unionWith may only read {', '.join(COLLECTIONS)}.")
        if isinstance(spec, dict) and "pipeline" in spec:
            check_pipeline(spec["pipeline"], f"{where}.$unionWith.pipeline")
    elif name == "$facet":
        for facet_name, sub in spec.items():
            check_pipeline(sub, f"{where}.$facet.{facet_name}")


def _check_no_javascript(value: Any) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            if key in FORBIDDEN_OPERATORS:
                raise RuleViolation(f"{key} runs JavaScript on the server and is not allowed.")
            _check_no_javascript(inner)
    elif isinstance(value, list):
        for inner in value:
            _check_no_javascript(inner)


def _run(collection: str, pipeline: list[dict[str, Any]]) -> LabResult:
    started = time.perf_counter()
    docs = analytics_repo.run_untrusted_pipeline(collection, pipeline, MAX_ROWS)
    millis = (time.perf_counter() - started) * 1000
    truncated = len(docs) > MAX_ROWS
    docs = docs[:MAX_ROWS]
    columns: list[str] = []
    for doc in docs:
        columns += [key for key in doc if key not in columns]
    rows = [[_cell(doc[c]) if c in doc else "" for c in columns] for doc in docs]
    return LabResult(collection, to_ejson(pipeline, indent=2), columns, rows, round(millis, 1), truncated)


def _cell(value: Any) -> str:
    """Strings as-is; everything else as compact Extended JSON (dates, ids, nested docs)."""
    return value if isinstance(value, str) else to_ejson(value)
