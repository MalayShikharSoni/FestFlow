"""
Python and mongosh must agree (spec: "the JS and Python versions must produce the same results").

* 00_setup.js builds exactly the validators and indexes that init_db builds.
* 03_aggregations.js returns exactly what analytics_repo returns — values AND BSON types.
* 05_verify_data.js reaches the same verdict as verify_data.
"""
from collections.abc import Iterator
from typing import Any

import pytest
from bson import json_util
from bson.json_util import JSONMode, JSONOptions
from pymongo.database import Database

from core.db import get_client
from core.management.commands.verify_data import run_checks
from repositories.analytics_repo import NAMED_PIPELINES
from tests.mongosh import needs_mongosh, run_script
from tests.test_aggregations import ids  # noqa: F401  (pytest fixture: the hand-built dataset)

pytestmark = needs_mongosh
CANONICAL = JSONOptions(json_mode=JSONMode.CANONICAL)


def _numbers_as_float(value: Any) -> Any:
    """mongosh writes `minimum: 1` as a double, Python as an int; for validators only the value matters."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return {k: _numbers_as_float(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_numbers_as_float(v) for v in value]
    return value


def _schema_and_indexes(db: Database) -> dict[str, Any]:
    out = {}
    for info in db.list_collections():
        indexes = {name: {k: v for k, v in spec.items() if k != "v"}
                   for name, spec in db[info["name"]].index_information().items()}
        out[info["name"]] = _numbers_as_float({"options": info["options"], "indexes": indexes})
    return out


@pytest.fixture
def js_db() -> Iterator[Database]:
    client = get_client()
    name = "spectra_test_js"
    client.drop_database(name)
    yield client[name]
    client.drop_database(name)


def test_setup_js_builds_the_same_validators_and_indexes(test_db: Database, js_db: Database) -> None:
    run_script(js_db.name, "00_setup.js")
    assert _schema_and_indexes(js_db) == _schema_and_indexes(test_db)


def _typed(value: Any) -> Any:
    """Pair every leaf with its type name, so int 8 and double 8.0 are told apart."""
    if isinstance(value, dict):
        return {k: _typed(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_typed(v) for v in value]
    return (type(value).__name__, value)


def test_aggregations_js_match_python_exactly(test_db: Database, ids: dict) -> None:  # noqa: F811
    output = run_script(test_db.name, "03_aggregations.js", env={"FESTFLOW_PARITY": "1"})
    js_results = dict(line.split("\t", 1) for line in output.splitlines() if "\t" in line)
    assert set(js_results) == set(NAMED_PIPELINES)
    for name, named in NAMED_PIPELINES.items():
        python_rows = list(test_db[named.collection].aggregate(named.build()))
        assert len(python_rows) > 0, f"{name} returned nothing on the fixture"
        # Same decoder on both sides (Extended JSON round trip), so datetimes compare like for like.
        python_side = json_util.loads(json_util.dumps(python_rows, json_options=CANONICAL))
        assert _typed(python_side) == _typed(json_util.loads(js_results[name])), name


def test_verify_js_agrees_with_python(test_db: Database, ids: dict) -> None:  # noqa: F811
    assert all(not problems for _, problems in run_checks(test_db))
    output = run_script(test_db.name, "05_verify_data.js")
    assert "All invariants hold." in output
    assert "FAIL" not in output
