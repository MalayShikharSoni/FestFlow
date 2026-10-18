"""Query Lab guards: read-only allowlist, no JavaScript, our collections only, row cap, verbatim errors."""
import pytest
from bson import ObjectId
from pymongo.database import Database

from core.exceptions import QueryFailed, RuleViolation
from services import analytics_service
from tests import factories as f


@pytest.mark.parametrize("pipeline,message", [
    ('[{"$out": "stolen"}]', "read-only"),
    ('[{"$merge": {"into": "x"}}]', "read-only"),
    ('[{"$facet": {"a": [{"$merge": "x"}]}}]', "read-only"),
    ('[{"$lookup": {"from": "events", "as": "e", "pipeline": [{"$out": "x"}]}}]', "read-only"),
    ('[{"$match": {"$where": "sleep(10000)"}}]', "JavaScript"),
    ('[{"$project": {"x": {"$function": {"body": "1", "args": [], "lang": "js"}}}}]', "JavaScript"),
    ('[{"$lookup": {"from": "system.views", "localField": "a", "foreignField": "b", "as": "c"}}]', "may only read"),
    ('[{"$unionWith": "secret_collection"}]', "may only read"),
    ('[{"$collStats": {}}]', "not allowed"),
    ('[{"$match": {}, "$limit": 1}]', "exactly one stage"),
    ('{"$match": {}}', "JSON array"),
    ("[]", "JSON array"),
    ("[{$match: 1}", "Not valid JSON"),
])
def test_rejected_pipelines(pipeline: str, message: str) -> None:
    with pytest.raises(RuleViolation, match=message):
        analytics_service.run_custom("bookings", pipeline)


def test_unknown_collection_is_rejected() -> None:
    with pytest.raises(RuleViolation, match="Collection"):
        analytics_service.run_custom("system.users", "[{\"$match\": {}}]")


def test_custom_pipeline_with_extended_json(db: Database) -> None:
    venue_id = f.insert(db, "venues", f.venue_doc(name="Lab Hall"))
    result = analytics_service.run_custom(
        "venues", f'[{{"$match": {{"_id": {{"$oid": "{venue_id}"}}}}}}, {{"$project": {{"_id": 0, "name": 1}}}}]')
    assert result.columns == ["name"]
    assert result.rows == [["Lab Hall"]]
    assert not result.truncated


def test_results_are_capped(db: Database) -> None:
    db.venues.insert_many([f.venue_doc() for _ in range(analytics_service.MAX_ROWS + 5)])
    result = analytics_service.run_custom("venues", '[{"$project": {"name": 1}}]')
    assert len(result.rows) == analytics_service.MAX_ROWS
    assert result.truncated


def test_server_errors_are_shown_verbatim() -> None:
    with pytest.raises(QueryFailed, match=r"Unknown expression \$bogus"):
        analytics_service.run_custom("bookings", '[{"$project": {"x": {"$bogus": 1}}}]')


def test_every_named_pipeline_runs(db: Database) -> None:
    for named in analytics_service.named_pipelines():
        result = analytics_service.run_named(named.name)
        assert result.name == named.name and result.pipeline_json.startswith("[")


def test_named_pipeline_shows_object_ids_as_extended_json(db: Database) -> None:
    venue_id = f.insert(db, "venues", f.venue_doc())
    f.insert(db, "events", f.event_doc(venue_id, ticketTiers=[f.tier(sold=5)]))
    result = analytics_service.run_named("occupancy_per_tier")
    event_id_cell = result.rows[0][result.columns.index("eventId")]
    assert event_id_cell.startswith('{"$oid"') and ObjectId.is_valid(event_id_cell.split('"')[3])
