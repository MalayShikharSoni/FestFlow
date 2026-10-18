"""
Test setup: every test runs against MONGO_TEST_DB_NAME (spectra_test), never the real DB.

* Session start: point settings at the test DB, drop it, create collections with
  validators + indexes (the same init_database() that init_db runs). Session end: drop it.
* Before each test: delete_many({}) on every collection. Unlike drop(), this keeps
  the validators and indexes, so each test starts empty but fully constrained.
"""
from collections.abc import Iterator

import pytest
from django.conf import settings
from pymongo.database import Database

from core.db import get_client
from core.management.commands.init_db import init_database

COLLECTIONS = ("venues", "events", "users", "bookings")


@pytest.fixture(scope="session", autouse=True)
def test_db() -> Iterator[Database]:
    name = settings.MONGO_TEST_DB_NAME
    assert name != settings.MONGO_DB_NAME, "MONGO_TEST_DB_NAME must differ from MONGO_DB_NAME"
    settings.MONGO_DB_NAME = name  # repositories call get_db(), which reads this setting
    client = get_client()
    client.drop_database(name)
    db = client[name]
    init_database(db)
    yield db
    client.drop_database(name)


@pytest.fixture(autouse=True)
def empty_collections(test_db: Database) -> Database:
    for name in COLLECTIONS:
        test_db[name].delete_many({})
    return test_db


@pytest.fixture
def db(test_db: Database) -> Database:
    return test_db
