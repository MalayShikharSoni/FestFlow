"""Public pages: event list (filters, $text search, pagination), event detail, health."""
from datetime import date

from bson import ObjectId
from django.contrib.auth.hashers import check_password, make_password
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.http import urlencode

from core.auth import get_session_user
from core.db import health_check
from core.exceptions import NotFoundError
from core.schemas import EVENT_TYPES
from services import event_service
from web.forms import BookingForm


def event_list(request: HttpRequest) -> HttpResponse:
    event_type = request.GET.get("type") if request.GET.get("type") in EVENT_TYPES else None
    days = event_service.fest_days()
    day = _parse_day(request.GET.get("day"), days)
    q = (request.GET.get("q") or "").strip()[:100]
    page = int(request.GET["page"]) if request.GET.get("page", "").isdigit() else 1

    data = event_service.list_events(event_type, day, q or None, page)
    filters = {k: v for k, v in {"type": event_type, "day": day and day.isoformat(), "q": q}.items() if v}
    return render(request, "web/event_list.html", {
        **data,
        "days": days,
        "selected_type": event_type,
        "selected_day": day,
        "q": q,
        "filters_qs": urlencode(filters),  # kept when paging
        "types": EVENT_TYPES,
    })


def _parse_day(value: str | None, days: list[date]) -> date | None:
    try:
        day = date.fromisoformat(value) if value else None
    except ValueError:
        return None
    return day if day in days else None


def event_detail(request: HttpRequest, event_id: ObjectId) -> HttpResponse:
    try:
        event = event_service.get_event_detail(event_id)
    except NotFoundError as exc:
        raise Http404(str(exc)) from exc
    form = BookingForm(event=event) if get_session_user(request) else None
    return render(request, "web/event_detail.html", {"event": event, "form": form})


def _hashers_work_without_auth_app() -> bool:
    """make_password/check_password run with django.contrib.auth NOT installed."""
    hashed = make_password("Spectra@2026")
    return check_password("Spectra@2026", hashed) and not check_password("wrong", hashed)


def health(request: HttpRequest) -> HttpResponse:
    """Ping MongoDB and list collections — the Phase 0 smoke test."""
    context = {"mongo": health_check(), "hashers_ok": _hashers_work_without_auth_app()}
    return render(request, "web/health.html", context, status=200 if context["mongo"]["ok"] else 503)
