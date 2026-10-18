"""
python manage.py train_noshow [--test-size 0.25] [--seed 2026] [--out PATH]

Optional (Phase 7). Trains the no-show LogisticRegression on bookings of
completed events (features come from the noshow_features aggregation), prints
accuracy / ROC-AUC on a held-out split, and saves ml/artifacts/noshow.joblib.
The organizer dashboard shows a forecast card only while that file exists.
"""
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

CAVEAT = ("Caveat: the data is synthetic. The seed decides no-shows from these same signals "
          "(free/cheap tickets, early bookings, per-user flakiness), so the model mostly relearns "
          "what the seed injected. It demonstrates MongoDB -> features -> model, not a real-world finding.")


class Command(BaseCommand):
    help = "Train the optional no-show predictor and save it to ml/artifacts/noshow.joblib."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--test-size", type=float, default=0.25)
        parser.add_argument("--seed", type=int, default=2026)
        parser.add_argument("--out", type=Path, default=None)

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            from ml import noshow
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise CommandError(f"scikit-learn/joblib are not installed: {exc}") from exc
        try:
            result = noshow.train(options["test_size"], options["seed"], options["out"])
        except (ImportError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        m = result.metrics
        self.stdout.write(f"Bookings of completed events: {m['rows']} (train {m['train']}, test {m['test']}); "
                          f"no-show rate {m['noShowRate']:.1%}")
        self.stdout.write(f"  accuracy  {m['accuracy']:.3f}   (always guessing 'shows up' scores "
                          f"{m['baselineAccuracy']:.3f})")
        self.stdout.write(f"  ROC-AUC   {m['rocAuc']:.3f}   (0.5 = coin toss, 1.0 = perfect ranking)")
        self.stdout.write("  strongest signals (standardized coefficients; + means more likely to miss):")
        for name, coef in result.coefficients[:6]:
            self.stdout.write(f"    {name:15} {coef:+.3f}")
        self.stdout.write(self.style.WARNING(CAVEAT))
        self.stdout.write(self.style.SUCCESS(f"Saved {result.path}"))
