"""One place to ask for "now", so services and tests agree on the time."""
from datetime import datetime, timezone


def now() -> datetime:
    """Current UTC time, truncated to milliseconds.

    BSON dates store milliseconds, so a Python datetime with microseconds would
    come back from MongoDB slightly different. Truncating first means a value we
    write compares equal to the value we later read back.
    """
    current = datetime.now(timezone.utc)
    return current.replace(microsecond=current.microsecond // 1000 * 1000)
