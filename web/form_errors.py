"""Put database-level errors back onto the form field the user typed into."""
import re

from django import forms

from core.exceptions import DuplicateError, ValidationFailed

MISSING = re.compile(r"^missing (\w+)")


def apply_db_errors(form: forms.Form, exc: ValidationFailed | DuplicateError,
                    aliases: dict[str, str] | None = None) -> None:
    """Attach validator (code 121) or unique-index (11000) errors to form fields.

    `aliases` maps document field names to form field names, e.g. {"startTime": "start"}.
    Anything that does not map to a field becomes a non-field error, so nothing is lost.
    """
    aliases = aliases or {}
    if isinstance(exc, DuplicateError):
        for field in exc.fields or ["__all__"]:
            _add(form, aliases.get(field, field), "This value is already taken.")
        return
    for path, message in exc.problems:
        root = re.split(r"[.\[]", path, maxsplit=1)[0]
        missing = MISSING.match(message)
        if root == "document" and missing:
            root = missing.group(1)
            message = "This field is required for this type."
        _add(form, aliases.get(root, root), message, path)


def _add(form: forms.Form, field: str, message: str, path: str = "") -> None:
    if field in form.fields:
        form.add_error(field, message)
    else:
        form.add_error(None, f"{path}: {message}" if path and path != "document" else message)


def problems_text(exc: ValidationFailed) -> str:
    """One-line summary for a flash message."""
    return "; ".join(f"{p}: {m}" if p != "document" else m for p, m in exc.problems)
