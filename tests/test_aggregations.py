"""
Aggregation pipelines against a tiny hand-built fixture with numbers you can check on paper.

Fixture (times in UTC; IST = UTC+5:30):
  Venue V: sections A (100 seats), B (50 seats)
  E1 "Alpha Night"   pro_show, completed, starts 2025-12-10 13:30 UTC (19:00 IST)
                     GOLD  1000/ticket cap 10 sold 3 (A) · SILVER 500 cap 20 sold 2 (B)
  E2 "Beta Fest"     cultural, completed, starts 2025-12-11 10:30 UTC (16:00 IST)
                     FREE  0 cap 10 sold 2 (A) · GA 100 cap 10 sold 1 (B)
  E3 "Gamma Workshop" workshop, scheduled 2030, seatLimit 5, seatsTaken 1
  Users U1, U2, U3 (attendees), V1, V2 (volunteers)

  booking  user event tickets          total mode  status     bookedAt (IST)        check-ins (IST)
  B1       U1   E1    GOLD x2           2000  UPI   confirmed  08 Dec 19:00 (-2d)    1 at 18:30
  B2       U2   E1    GOLD x1           1000  CARD  confirmed  30 Nov 19:00 (-10d)   1 at 18:40
  B3       U2   E1    SILVER x2         1000  UPI   confirmed  10 Dec 07:00 (same)   2 at 18:50
  B4       U3   E1    SILVER x1          500  UPI   refunded   31 Oct 19:00 (-40d)   -
  B5       U3   E2    FREE x2              0  FREE  confirmed  08 Dec 16:00 (-3d)    1 at 15:30 (11 Dec)
  B6       U1   E2    GA x1              100  CASH  confirmed  10 Dec 16:00 (-1d)    -
  B7       U1   E3    workshop x1          0  FREE  confirmed  01 Dec 05:30          -
  B8       U2   E3    workshop x1          0  FREE  cancelled  02 Dec 05:30          -
"""
from datetime import timedelta
from typing import Any

import pytest
from bson import ObjectId
from pymongo.database import Database

from repositories import analytics_repo as analytics
from tests import factories as f

E1_START = f.utc(2025, 12, 10, 13, 30)
E2_START = f.utc(2025, 12, 11, 10, 30)
E3_START = f.utc(2030, 1, 15, 4, 30)


@pytest.fixture
def ids(db: Database) -> dict[str, ObjectId]:
    ids: dict[str, ObjectId] = {}
    ids["V"] = f.insert(db, "venues", f.venue_doc(name="Main Hall"))
    ids["E1"] = f.insert(db, "events", f.event_doc(
        ids["V"], "pro_show", start=E1_START, hours=3.5, title="Alpha Night", status="completed",
        ticketTiers=[f.tier("GOLD", 1000, 10, 3, "A"), f.tier("SILVER", 500, 20, 2, "B")]))
    ids["E2"] = f.insert(db, "events", f.event_doc(
        ids["V"], "cultural", start=E2_START, hours=2, title="Beta Fest", status="completed",
        ticketTiers=[f.tier("FREE", 0, 10, 2, "A"), f.tier("GA", 100, 10, 1, "B")]))
    ids["E3"] = f.insert(db, "events", f.event_doc(
        ids["V"], "workshop", start=E3_START, title="Gamma Workshop", seatLimit=5, seatsTaken=1))
    for name in ("U1", "U2", "U3"):
        ids[name] = f.insert(db, "users", f.user_doc(name=name))
    ids["V1"] = f.insert(db, "users", f.user_doc("volunteer", name="Vol One", shifts=[
        {"eventId": ids["E1"], "duty": "gate", "start": E1_START, "end": E1_START + timedelta(hours=3.5)},
        {"eventId": ids["E2"], "duty": "stage", "start": E2_START, "end": E2_START + timedelta(hours=1)},
    ]))
    ids["V2"] = f.insert(db, "users", f.user_doc("volunteer", name="Vol Two", shifts=[
        {"eventId": ids["E1"], "duty": "help_desk", "start": E1_START, "end": E1_START + timedelta(minutes=105)},
    ]))

    def t(tier_id: str | None, price: int, checked: Any = None) -> dict[str, Any]:
        return f.ticket(tier_id=tier_id, price=price, checked_in_at=checked)

    ist = lambda d, h, m=0: f.utc(2025, 12, d, h, m) - timedelta(hours=5, minutes=30)  # noqa: E731
    rows = [
        (ids["U1"], ids["E1"], "pro_show", [t("GOLD", 1000, ist(10, 18, 30)), t("GOLD", 1000)], "UPI",
         "confirmed", E1_START - timedelta(days=2), None),
        (ids["U2"], ids["E1"], "pro_show", [t("GOLD", 1000, ist(10, 18, 40))], "CARD",
         "confirmed", E1_START - timedelta(days=10), None),
        (ids["U2"], ids["E1"], "pro_show", [t("SILVER", 500, ist(10, 18, 50)), t("SILVER", 500, ist(10, 18, 50))],
         "UPI", "confirmed", E1_START - timedelta(hours=12), None),
        (ids["U3"], ids["E1"], "pro_show", [t("SILVER", 500)], "UPI",
         "refunded", E1_START - timedelta(days=40), E1_START - timedelta(days=39)),
        (ids["U3"], ids["E2"], "cultural", [t("FREE", 0, ist(11, 15, 30)), t("FREE", 0)], "FREE",
         "confirmed", E2_START - timedelta(days=3), None),
        (ids["U1"], ids["E2"], "cultural", [t("GA", 100)], "CASH",
         "confirmed", E2_START - timedelta(days=1), None),
        (ids["U1"], ids["E3"], "workshop", [t(None, 0)], "FREE", "confirmed", f.utc(2025, 12, 1), None),
        (ids["U2"], ids["E3"], "workshop", [t(None, 0)], "FREE", "cancelled", f.utc(2025, 12, 2), f.utc(2025, 12, 3)),
    ]
    for user, event, etype, tickets, mode, status, booked, cancelled in rows:
        f.insert(db, "bookings", f.booking_doc(user, event, tickets, etype, status, mode, booked, cancelled))
    return ids


def test_revenue_by_event_and_tier(ids: dict) -> None:
    rows = [(r["title"], r["tierId"], r["revenue"], r["tickets"]) for r in analytics.revenue_by_event_and_tier()]
    assert rows == [
        ("Alpha Night", "GOLD", 3000, 3),
        ("Alpha Night", "SILVER", 1000, 2),  # the refunded SILVER ticket is not revenue
        ("Beta Fest", "GA", 100, 1),
        ("Beta Fest", "FREE", 0, 2),
        ("Gamma Workshop", None, 0, 1),
    ]


def test_occupancy_per_tier(ids: dict) -> None:
    rows = [(r["title"], r["tierId"], r["occupancyPct"], r["sectionCapacity"]) for r in analytics.occupancy_per_tier()]
    assert rows == [
        ("Alpha Night", "GOLD", 30.0, 100),
        ("Beta Fest", "FREE", 20.0, 100),
        ("Alpha Night", "SILVER", 10.0, 50),
        ("Beta Fest", "GA", 10.0, 50),
    ]


def test_checkin_traffic_by_hour_in_ist(ids: dict) -> None:
    rows = [(r["label"], r["checkIns"]) for r in analytics.checkin_traffic_by_hour()]
    assert rows == [("10-12 18:00", 4), ("11-12 15:00", 1)]


def test_checkin_traffic_for_one_event(ids: dict) -> None:
    rows = analytics.checkin_traffic_by_hour(ids["E2"])
    assert [(r["label"], r["checkIns"]) for r in rows] == [("11-12 15:00", 1)]


def test_no_show_rate_per_event(ids: dict) -> None:
    rows = [(r["title"], r["sold"], r["checkedIn"], r["noShowPct"]) for r in analytics.no_show_rate_per_event()]
    assert rows == [("Beta Fest", 3, 1, 66.7), ("Alpha Night", 5, 4, 20.0)]  # Gamma is not completed


def test_booking_lead_time_buckets(ids: dict) -> None:
    rows = [(r["bucket"], r["bookings"], r["avgTickets"]) for r in analytics.booking_lead_time_buckets()]
    assert rows == [(0, 1, 2.0), (1, 2, 1.5), (3, 1, 2.0), (7, 1, 1.0), ("30+", 3, 1.0)]


def test_top_spenders(ids: dict) -> None:
    rows = [(r["rank"], r["name"], r["totalSpent"], r["bookings"]) for r in analytics.top_spenders()]
    assert rows == [(1, "U1", 2100, 3), (2, "U2", 2000, 2), (3, "U3", 0, 1)]


def test_revenue_by_payment_mode(ids: dict) -> None:
    rows = [(r["mode"], r["revenue"], r["bookings"], r["sharePct"]) for r in analytics.revenue_by_payment_mode()]
    assert rows == [("UPI", 3000, 2, 73.2), ("CARD", 1000, 1, 24.4), ("CASH", 100, 1, 2.4), ("FREE", 0, 2, 0.0)]


def test_cancellation_rate_by_event_type(ids: dict) -> None:
    rows = [(r["eventType"], r["bookings"], r["cancelled"], r["refunded"], r["cancellationPct"])
            for r in analytics.cancellation_rate_by_event_type()]
    assert rows == [("workshop", 2, 1, 0, 50.0), ("pro_show", 4, 0, 1, 25.0), ("cultural", 2, 0, 0, 0.0)]


def test_daily_sales_with_moving_avg(ids: dict) -> None:
    rows = analytics.daily_sales_with_moving_avg()
    ist_days = [(r["day"] + timedelta(hours=5, minutes=30)).strftime("%d %b") for r in rows]
    assert ist_days == ["30 Nov", "01 Dec", "08 Dec", "10 Dec"]
    assert [r["tickets"] for r in rows] == [1, 1, 4, 3]
    # Range window [-2, 0] days: 10 Dec averages 08 Dec and 10 Dec only (09 Dec had no sales).
    assert [r["movingAvg3d"] for r in rows] == [1.0, 1.0, 4.0, 3.5]


def test_volunteer_hours_and_coverage(ids: dict) -> None:
    hours = [(r["name"], r["shifts"], r["hours"]) for r in analytics.volunteer_hours()]
    assert hours == [("Vol One", 2, 4.5), ("Vol Two", 1, 1.8)]
    coverage = [(r["title"], r["total"], r["byDuty"]) for r in analytics.coverage_per_event()]
    # Dict equality ignores key order ($push order inside a $group is not guaranteed).
    assert coverage == [("Alpha Night", 2, {"gate": 1, "help_desk": 1}), ("Beta Fest", 1, {"stage": 1})]


def test_organizer_dashboard_facet(ids: dict) -> None:
    dashboard = analytics.organizer_dashboard()
    assert dashboard["kpis"] == {"bookings": 8, "confirmed": 6, "ticketsSold": 9, "revenue": 4100,
                                 "checkedIn": 5, "cancellationPct": 25.0}
    assert [(e["title"], e["revenue"]) for e in dashboard["topEvents"]][:2] == [("Alpha Night", 4000), ("Beta Fest", 100)]
    assert [(m["mode"], m["bookings"]) for m in dashboard["paymentModes"]] == [
        ("FREE", 2), ("UPI", 2), ("CARD", 1), ("CASH", 1)]
    assert [d["tickets"] for d in dashboard["salesByDay"]] == [1, 1, 4, 3]


def test_empty_database_gives_empty_results(db: Database) -> None:
    assert analytics.revenue_by_event_and_tier() == []
    assert analytics.organizer_dashboard()["kpis"] is None
