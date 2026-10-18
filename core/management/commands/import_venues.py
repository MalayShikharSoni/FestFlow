"""
python manage.py import_venues data/sample_venues.json

Idempotent venue import: each venue is upserted by name, so running the same
file twice creates nothing new the second time (it just re-applies the values).
Goes through event_service, so capacity checks against existing tiers still apply.
"""
import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from core.exceptions import FestFlowError
from services.event_service import VenueInput, import_venues


class Command(BaseCommand):
    help = "Upsert venues from a JSON file (a list of {name, block, sections, facilities})."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("path", type=Path)

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            rows = json.loads(options["path"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Cannot read {options['path']}: {exc}") from exc
        venues = [
            VenueInput(name=row["name"], block=row["block"],
                       sections=[(s["name"], int(s["capacity"])) for s in row["sections"]],
                       facilities=row.get("facilities", []))
            for row in rows
        ]
        try:
            created, updated = import_venues(venues)
        except FestFlowError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"{created} venue(s) created, {updated} updated."))
