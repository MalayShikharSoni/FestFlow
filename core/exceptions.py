"""
Application exceptions.

Services raise these and views catch them, so views never need to import PyMongo
error classes. ValidationFailed also translates MongoDB's errInfo (a deeply
nested explanation of why a document broke the validator) into short
(field, message) pairs that a template can show.
"""
from typing import Any

from pymongo.errors import WriteError

from core.schemas import rule_name

DOCUMENT_VALIDATION_FAILURE = 121  # MongoDB error code for a validator rejection

Problem = tuple[str, str]  # (field path, message), e.g. ("tickets[0].code", "regular expression did not match")


class FestFlowError(Exception):
    """Base class for errors we expect and show to the user."""


class NotFoundError(FestFlowError):
    """The requested document does not exist."""


class SoldOutError(FestFlowError):
    """Not enough seats left in the tier/workshop for the requested quantity."""


class RuleViolation(FestFlowError):
    """A business rule enforced in services/ (one the validator cannot express) was broken."""


class ValidationFailed(FestFlowError):
    """MongoDB rejected a write because it broke the collection's validator (code 121)."""

    def __init__(self, collection: str, problems: list[Problem], err_info: dict[str, Any] | None = None):
        self.collection = collection
        self.problems = problems
        self.err_info = err_info or {}
        summary = "; ".join(f"{field}: {msg}" for field, msg in problems)
        super().__init__(f"{collection} validation failed — {summary}")

    @property
    def fields(self) -> list[str]:
        return [field for field, _ in self.problems]

    @classmethod
    def from_write_error(cls, exc: WriteError, collection: str) -> "ValidationFailed":
        err_info = (exc.details or {}).get("errInfo", {})
        return cls(collection, describe_err_info(collection, err_info), err_info)


def is_validation_error(exc: Exception) -> bool:
    return isinstance(exc, WriteError) and exc.code == DOCUMENT_VALIDATION_FAILURE


# --- errInfo parsing ---------------------------------------------------------
# Our validators are {"$and": [{"$jsonSchema": ...}, {"$expr": ...}, ...]}, so
# errInfo.details lists the failed $and clauses by index: 0 is the schema (with a
# tree of failed JSON Schema keywords), 1+ are our named $expr rules.

def describe_err_info(collection: str, err_info: dict[str, Any]) -> list[Problem]:
    details = err_info.get("details", {})
    problems: list[Problem] = []
    for clause in details.get("clausesNotSatisfied", []):
        if clause.get("index") == 0:
            rules = clause.get("details", {}).get("schemaRulesNotSatisfied", [])
            problems.extend(_schema_problems(rules, ""))
        else:
            problems.append(("document", rule_name(collection, clause["index"])))
    return problems or [("document", "failed validation")]


def _join(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


def _schema_problems(rules: list[dict[str, Any]], path: str) -> list[Problem]:
    """Walk the tree of failed JSON Schema keywords, building a field path as we go."""
    here = path or "document"
    out: list[Problem] = []
    for rule in rules:
        op = rule.get("operatorName")
        if op == "properties":
            for prop in rule.get("propertiesNotSatisfied", []):
                out.extend(_schema_problems(prop.get("details", []), _join(path, prop["propertyName"])))
        elif op == "items":
            out.extend(_schema_problems(rule.get("details", []), f"{path}[{rule.get('itemIndex')}]"))
        elif op == "required":
            out.append((here, "missing " + ", ".join(rule.get("missingProperties", []))))
        elif op == "additionalProperties":
            out.append((here, "unexpected field(s) " + ", ".join(rule.get("additionalProperties", []))))
        elif op == "oneOf":
            out.extend(_one_of_problems(rule, path))
        elif op == "not":
            out.append((here, "contains a field that is not allowed for this type"))
        else:
            out.append((here, _leaf_message(rule)))
    return out


def _leaf_message(rule: dict[str, Any]) -> str:
    """e.g. "comparison failed (expected minimum 1, got 0)"."""
    message = rule.get("reason", f"failed {rule.get('operatorName')}")
    hints = []
    for key, value in (rule.get("specifiedAs") or {}).items():
        if key != "pattern":  # patterns are long and already implied by the reason
            hints.append(f"expected {key} {value}")
    if "consideredValue" in rule:
        value = rule["consideredValue"]
        hints.append(f"got {len(value)} items" if isinstance(value, list) else f"got {value!r}")
    return f"{message} ({', '.join(hints)})" if hints else message


def _one_of_problems(rule: dict[str, Any], path: str) -> list[Problem]:
    """oneOf reports every branch. Show only the branch for the document's own type.

    The other branches failed simply because `type` did not match their enum,
    which is noise. If every branch failed on `type`, the type itself is invalid
    and the top-level `type` enum failure already says so.
    """
    for branch in rule.get("schemasNotSatisfied", []):
        branch_rules = branch.get("details", [])
        if not _fails_on_type(branch_rules):
            return _schema_problems(branch_rules, path)
    return []


def _fails_on_type(branch_rules: list[dict[str, Any]]) -> bool:
    for rule in branch_rules:
        if rule.get("operatorName") == "properties":
            if any(p.get("propertyName") == "type" for p in rule.get("propertiesNotSatisfied", [])):
                return True
    return False
