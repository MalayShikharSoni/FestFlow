"""Phase 7: training on seeded data, the forecast, and the hidden dashboard card."""
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client
from pymongo.database import Database

from tests import factories as f

pytest.importorskip("sklearn")
from ml import noshow  # noqa: E402


@pytest.fixture(scope="module")
def seeded(test_db: Database) -> Database:
    call_command("seed", reset=True, bookings=1500, db=settings.MONGO_TEST_DB_NAME, stdout=StringIO())
    return test_db


@pytest.fixture
def empty_collections(test_db: Database) -> Database:
    """Override the global per-test wipe for tests that use the seeded data."""
    return test_db


def test_train_and_forecast(seeded: Database, tmp_path: Path) -> None:
    model_path = tmp_path / "noshow.joblib"
    result = noshow.train(path=model_path)
    m = result.metrics
    assert model_path.exists()
    assert m["train"] + m["test"] == m["rows"] > 500
    assert 0.0 <= m["rocAuc"] <= 1.0 and 0.0 <= m["accuracy"] <= 1.0
    assert {name for name, _ in result.coefficients} == set(noshow.FEATURE_NAMES)

    forecast = noshow.forecast_upcoming(model_path)
    assert forecast is not None and forecast["events"]
    for event in forecast["events"]:
        assert 0 <= event["noShowPct"] <= 100
        assert 0 <= event["expectedNoShows"] <= event["tickets"]
    assert seeded.events.count_documents({"status": "scheduled"}) >= len(forecast["events"])


def test_no_model_means_no_forecast_and_no_card(seeded: Database, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing.joblib"
    assert noshow.forecast_upcoming(missing) is None
    monkeypatch.setattr(noshow, "default_model_path", lambda: missing)
    seeded.users.insert_one(f.user_doc("organizer", email="ml-boss@test.example"))
    client = Client()
    client.post("/login/", {"email": "ml-boss@test.example", "password": f.PASSWORD})
    page = client.get("/organizer/").content.decode()
    assert "Organizer dashboard" in page
    assert "Predicted no-shows" not in page


def test_dashboard_shows_the_card_when_a_model_exists(seeded: Database, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    model_path = tmp_path / "noshow.joblib"
    noshow.train(path=model_path)
    monkeypatch.setattr(noshow, "default_model_path", lambda: model_path)
    seeded.users.insert_one(f.user_doc("organizer", email="ml-boss2@test.example"))
    client = Client()
    client.post("/login/", {"email": "ml-boss2@test.example", "password": f.PASSWORD})
    page = client.get("/organizer/").content.decode()
    assert "Predicted no-shows for upcoming events" in page
    assert "synthetic" in page  # the honest caveat is on screen
