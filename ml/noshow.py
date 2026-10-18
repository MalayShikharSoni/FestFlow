"""
No-show predictor (optional, Phase 7): MongoDB features -> logistic regression -> forecast.

Flow
  1. analytics_repo.noshow_features() — an aggregation pipeline builds one row of
     features per confirmed booking (lead time, price, event type/hour, and the
     user's past bookings / past no-show rate via $setWindowFields).
  2. to_matrix() turns those rows into numbers; train() fits a LogisticRegression
     on bookings of COMPLETED events (we know whether people came).
  3. forecast_upcoming() scores bookings of SCHEDULED events and sums the
     probabilities into an expected no-show % per event for the dashboard.

HONEST CAVEAT: the data is synthetic. The seed script decides no-shows from
exactly these kinds of signals (free/cheap tickets, early bookings, a hidden
per-user "flakiness"), so the model mostly RELEARNS the patterns the seed
injected. It demonstrates the MongoDB -> features -> model pipeline; it is not a
finding about real festival-goers.

scikit-learn/joblib are imported inside functions so the rest of the app works
even when they are not installed (the dashboard then hides the forecast card).
"""
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bson import ObjectId
from django.conf import settings

from repositories import analytics_repo, events_repo

EVENT_TYPES = ("pro_show", "cultural", "workshop")
FEATURE_NAMES = [
    "leadDays", "logPrice", "isFree", "ticketCount", "eventHour",
    "type_pro_show", "type_cultural", "type_workshop",
    "pastBookings", "pastNoShowRate", "hasHistory",
]


def default_model_path() -> Path:
    return Path(settings.BASE_DIR) / "ml" / "artifacts" / "noshow.joblib"


# --- Features ----------------------------------------------------------------
def to_vector(row: dict[str, Any], fill_rate: float) -> list[float]:
    """One booking's features as numbers, in FEATURE_NAMES order.

    log(1 + price): prices span 0-2999, and the jump from free to Rs.99 matters
    more than from Rs.1,999 to Rs.2,999. A user with no history gets the average
    no-show rate (fill_rate) plus hasHistory = 0, so the model can tell
    "unknown" apart from "never misses".
    """
    has_history = row["pastBookings"] > 0
    return [
        float(row["leadDays"]),
        math.log1p(row["ticketPrice"]),
        1.0 if row["ticketPrice"] == 0 else 0.0,
        float(row["ticketCount"]),
        float(row["eventHour"]),
        *[1.0 if row["eventType"] == t else 0.0 for t in EVENT_TYPES],
        float(row["pastBookings"]),
        float(row["pastNoShowRate"]) if has_history else fill_rate,
        1.0 if has_history else 0.0,
    ]


def labelled_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r["eventStatus"] == "completed" and r["noShow"] is not None]


# --- Training ----------------------------------------------------------------
@dataclass
class TrainResult:
    metrics: dict[str, float]
    coefficients: list[tuple[str, float]]
    path: Path


def train(test_size: float = 0.25, seed: int = 2026, path: Path | None = None) -> TrainResult:
    """Fit on completed-event bookings, report held-out metrics, save the model."""
    import joblib
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, roc_auc_score
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    rows = labelled_rows(analytics_repo.noshow_features())
    labels = [r["noShow"] for r in rows]
    if len(rows) < 50 or len(set(labels)) < 2:
        raise ValueError("Not enough completed-event bookings to train on. Seed the database first.")

    rates = [r["pastNoShowRate"] for r in rows if r["pastBookings"] > 0]
    fill_rate = sum(rates) / len(rates) if rates else 0.0
    matrix = [to_vector(r, fill_rate) for r in rows]
    x_train, x_test, y_train, y_test = train_test_split(
        matrix, labels, test_size=test_size, random_state=seed, stratify=labels)

    # Scaling puts every feature on the same footing, so the coefficients are comparable.
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
    model.fit(x_train, y_train)

    probabilities = model.predict_proba(x_test)[:, 1]
    majority = max(set(y_train), key=y_train.count)
    metrics = {
        "rows": len(rows),
        "train": len(x_train),
        "test": len(x_test),
        "noShowRate": round(sum(labels) / len(labels), 3),
        "accuracy": round(accuracy_score(y_test, model.predict(x_test)), 3),
        # Accuracy alone flatters an imbalanced problem: always guessing "shows up"
        # already scores this well. ROC-AUC measures how well the model RANKS risk.
        "baselineAccuracy": round(sum(1 for y in y_test if y == majority) / len(y_test), 3),
        "rocAuc": round(roc_auc_score(y_test, probabilities), 3),
    }
    coefficients = sorted(zip(FEATURE_NAMES, model[-1].coef_[0].round(3).tolist()),
                          key=lambda kv: -abs(kv[1]))

    path = path or default_model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "fill_rate": fill_rate, "features": FEATURE_NAMES, "metrics": metrics,
                 "coefficients": coefficients, "trained_at": datetime.now(timezone.utc)}, path)
    return TrainResult(metrics, coefficients, path)


# --- Forecast ----------------------------------------------------------------
def load(path: Path | None = None) -> dict[str, Any] | None:
    """The saved model bundle, or None if it was never trained or sklearn is missing.

    joblib files are pickles: only ever load ones this project wrote itself.
    """
    path = path or default_model_path()
    if not path.exists():
        return None
    try:
        import joblib
        bundle = joblib.load(path)
    except ImportError:
        return None
    return bundle if bundle.get("features") == FEATURE_NAMES else None  # stale model: ignore


def forecast_upcoming(path: Path | None = None) -> dict[str, Any] | None:
    """Expected no-show % per scheduled event, or None when there is no model."""
    bundle = load(path)
    if bundle is None:
        return None
    rows = [r for r in analytics_repo.noshow_features() if r["eventStatus"] == "scheduled"]
    per_event: dict[str, dict[str, float]] = {}
    if rows:
        probabilities = bundle["model"].predict_proba([to_vector(r, bundle["fill_rate"]) for r in rows])[:, 1]
        for row, p in zip(rows, probabilities):
            stats = per_event.setdefault(row["eventId"], {"tickets": 0, "expected": 0.0})
            stats["tickets"] += row["ticketCount"]
            stats["expected"] += p * row["ticketCount"]  # a no-show booking leaves all its seats empty
    events = events_repo.get_events_by_ids([ObjectId(event_id) for event_id in per_event])
    forecast = []
    for event_id, stats in per_event.items():
        event = events.get(ObjectId(event_id))
        if event is None:
            continue
        forecast.append({
            "title": event["title"], "startTime": event["startTime"], "type": event["type"],
            "tickets": int(stats["tickets"]), "expectedNoShows": round(stats["expected"]),
            "noShowPct": round(100 * stats["expected"] / stats["tickets"], 1),
        })
    forecast.sort(key=lambda f: f["startTime"])
    return {"events": forecast, "metrics": bundle["metrics"], "trained_at": bundle["trained_at"],
            "coefficients": bundle["coefficients"][:4]}
