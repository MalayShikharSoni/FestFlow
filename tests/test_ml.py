"""Phase 7: the no-show feature pipeline, checked against the hand-built fixture."""
import pytest

from repositories import analytics_repo
from tests.test_aggregations import ids  # noqa: F401  (pytest fixture: the hand-built dataset)

pytest.importorskip("sklearn")
from ml import noshow  # noqa: E402


def _row(rows: list[dict], booking_owner: str, event: str, ids: dict) -> dict:  # noqa: F811
    matches = [r for r in rows if r["userId"] == str(ids[booking_owner]) and r["eventId"] == str(ids[event])]
    assert len(matches) == 1
    return matches[0]


def test_features_pipeline_on_the_fixture(ids: dict) -> None:  # noqa: F811
    rows = analytics_repo.noshow_features()
    assert len(rows) == 6  # confirmed bookings only: B4 (refunded) and B8 (cancelled) are left out

    b1 = _row(rows, "U1", "E1", ids)  # GOLD x2 booked 2 days ahead, one ticket scanned
    assert (b1["noShow"], b1["leadDays"], b1["ticketPrice"], b1["ticketCount"], b1["eventHour"]) == (0, 2.0, 1000, 2, 19)
    assert (b1["pastBookings"], b1["pastNoShowRate"]) == (0, None)  # U1's first event

    b6 = _row(rows, "U1", "E2", ids)  # nothing scanned -> no-show; history = B1 (came)
    assert (b6["noShow"], b6["pastBookings"], b6["pastNoShowRate"]) == (1, 1, 0.0)

    b7 = _row(rows, "U1", "E3", ids)  # scheduled: no label yet; history = B1 (came) + B6 (missed)
    assert (b7["eventStatus"], b7["noShow"], b7["pastBookings"], b7["pastNoShowRate"]) == ("scheduled", None, 2, 0.5)

    b5 = _row(rows, "U3", "E2", ids)  # U3's earlier booking (B4) was refunded, so it is not history
    assert (b5["noShow"], b5["pastBookings"], b5["ticketPrice"]) == (0, 0, 0)


def test_feature_vector_layout() -> None:
    row = {"leadDays": 3.5, "ticketPrice": 0, "ticketCount": 2, "eventHour": 16, "eventType": "cultural",
           "pastBookings": 0, "pastNoShowRate": None}
    vector = noshow.to_vector(row, fill_rate=0.2)
    named = dict(zip(noshow.FEATURE_NAMES, vector))
    assert named["isFree"] == 1.0 and named["logPrice"] == 0.0
    assert (named["type_cultural"], named["type_pro_show"], named["type_workshop"]) == (1.0, 0.0, 0.0)
    assert (named["pastNoShowRate"], named["hasHistory"]) == (0.2, 0.0)  # unknown history -> average + flag
