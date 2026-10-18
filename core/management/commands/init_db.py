"""
python manage.py init_db [--db NAME]

Creates every collection with its validator, then builds every index.
Idempotent: safe to run again after editing core/schemas.py or core/indexes.py.
  * Collection missing -> create_collection(validator=...)
  * Collection exists  -> collMod, which replaces the validator in place
                          (existing documents are not re-checked; new writes are).
  * Index already there with the same definition -> create_indexes is a no-op.
  * Index name reused with a different definition -> we drop and rebuild it.
"""
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from pymongo.database import Database
from pymongo.errors import OperationFailure

from core.db import get_db
from core.indexes import INDEXES
from core.schemas import VALIDATION_ACTION, VALIDATION_LEVEL, VALIDATORS

# Server error codes for "an index with this name/key exists but with other options".
INDEX_CONFLICT_CODES = {85, 86}  # IndexOptionsConflict, IndexKeySpecsConflict


def apply_validators(db: Database) -> list[str]:
    """Create or update each collection's validator. Returns a log of what happened."""
    log = []
    existing = set(db.list_collection_names())
    for name, validator in VALIDATORS.items():
        options: dict[str, Any] = {
            "validator": validator,
            "validationLevel": VALIDATION_LEVEL,
            "validationAction": VALIDATION_ACTION,
        }
        if name in existing:
            db.command("collMod", name, **options)
            log.append(f"{name}: validator updated (collMod)")
        else:
            db.create_collection(name, **options)
            log.append(f"{name}: collection created with validator")
    return log


def apply_indexes(db: Database) -> list[str]:
    """Build every index from core/indexes.py. Returns a log of what happened."""
    log = []
    for coll_name, models in INDEXES.items():
        coll = db[coll_name]
        for model in models:
            index_name = model.document["name"]
            try:
                coll.create_indexes([model])
            except OperationFailure as exc:
                if exc.code not in INDEX_CONFLICT_CODES:
                    raise
                # Same name, different definition: the definition in indexes.py wins.
                coll.drop_index(index_name)
                coll.create_indexes([model])
                log.append(f"{coll_name}.{index_name}: rebuilt (definition changed)")
                continue
            log.append(f"{coll_name}.{index_name}: ok")
    return log


def init_database(db: Database) -> list[str]:
    """Validators first, then indexes (indexes need the collections to exist)."""
    return apply_validators(db) + apply_indexes(db)


class Command(BaseCommand):
    help = "Create collections with $jsonSchema/$expr validators and build all indexes (idempotent)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--db", default=settings.MONGO_DB_NAME,
                            help="Database name (default: MONGO_DB_NAME)")

    def handle(self, *args: Any, **options: Any) -> None:
        db = get_db(options["db"])
        self.stdout.write(f"Initialising database '{db.name}'")
        for line in init_database(db):
            self.stdout.write(f"  {line}")
        self.stdout.write(self.style.SUCCESS("Done."))
