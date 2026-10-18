"""The seed produces data that passes every invariant; verify_data catches planted faults."""
from io import StringIO

import pytest
from django.conf import settings
from django.core.management import call_command
from pymongo.database import Database

from core.management.commands.init_db import init_database
from core.management.commands.verify_data import run_checks
from tests.mongosh import needs_mongosh, run_script


@pytest.fixture(scope="module")
def seeded(test_db: Database) -> Database:
    """Seed once for the module (it takes a couple of seconds), into the test DB."""
    call_command("seed", reset=True, bookings=400, db=settings.MONGO_TEST_DB_NAME, stdout=StringIO())
    return test_db


@pytest.fixture(autouse=True)
def empty_collections(test_db: Database) -> Database:
    """Override the global per-test wipe: these tests share the seeded data."""
    return test_db


def test_seed_counts(seeded: Database) -> None:
    assert seeded.venues.count_documents({}) == 6
    assert seeded.events.count_documents({}) == 25
    assert seeded.users.count_documents({}) == 1500
    assert seeded.users.count_documents({"role": "volunteer"}) == 40
    assert seeded.bookings.count_documents({}) == 400
    assert seeded.users.count_documents({"email": {"$in": ["organizer@spectra.test", "attendee@spectra.test"]}}) == 2


def test_seeded_data_passes_every_check(seeded: Database) -> None:
    failures = {name: problems for name, problems in run_checks(seeded) if problems}
    assert failures == {}


def test_verify_data_catches_planted_faults(seeded: Database) -> None:
    db = seeded
    for name in ("events", "bookings", "users"):
        db.command("collMod", name, validationLevel="off")  # only to plant bad data
    try:
        event = db.events.find_one({"type": "pro_show"})
        db.events.update_one({"_id": event["_id"]}, {"$inc": {"ticketTiers.0.sold": 1}})
        db.bookings.update_one({"status": "confirmed"}, {"$set": {"eventType": "workshop"}})
        volunteer = db.users.find_one({"role": "volunteer", "shifts.0": {"$exists": True}})
        db.users.update_one({"_id": volunteer["_id"]}, {"$push": {"shifts": volunteer["shifts"][0]}})
        results = dict(run_checks(db))
        assert results["tier sold == confirmed tickets <= capacity"]
        assert any("eventType" in p for p in results["bookings reference real users/events and are consistent"])
        assert results["volunteer shifts do not overlap"]
    finally:
        init_database(db)  # validators back to strict
        call_command("seed", reset=True, bookings=400, db=db.name, stdout=StringIO())  # clean data again


# --- The other mongosh scripts run cleanly on seeded data -----------------------
@needs_mongosh
def test_validation_demo_rejects_everything_it_should(seeded: Database) -> None:
    output = run_script(seeded.name, "01_validation_demo.js")
    assert "0 unexpectedly accepted" in output
    assert seeded.events.count_documents({"slug": {"$regex": "^demo-"}}) == 0  # cleaned up


@needs_mongosh
def test_crud_script_runs_and_leaves_data_valid(seeded: Database) -> None:
    before = {name: seeded[name].count_documents({}) for name in ("venues", "events", "users", "bookings")}
    run_script(seeded.name, "02_crud.js")
    after = {name: seeded[name].count_documents({}) for name in before}
    assert after == before
    assert all(not problems for _, problems in run_checks(seeded))


@needs_mongosh
def test_explain_script_restores_every_index(seeded: Database) -> None:
    before = {name: sorted(seeded[name].index_information()) for name in ("events", "bookings")}
    output = run_script(seeded.name, "04_indexes_explain.js")
    assert "answered from the index alone" in output
    assert {name: sorted(seeded[name].index_information()) for name in before} == before
