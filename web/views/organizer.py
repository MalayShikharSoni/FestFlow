"""Organizer pages: dashboard, event and venue CRUD, gate check-in, volunteer shifts."""
from datetime import datetime
from typing import Any

from bson import ObjectId
from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core.auth import role_required
from core.exceptions import DuplicateError, FestFlowError, NotFoundError, ValidationFailed
from services import analytics_service, checkin_service, event_service, volunteer_service
from services.event_service import EventInput, TierInput, VenueInput
from web.form_errors import apply_db_errors
from web.forms import CheckInForm, EventForm, ShiftForm, TierFormSet, VenueForm, split_list

organizer_only = role_required("organizer")

# Document field -> form field, for errors coming back from the validator.
EVENT_ALIASES = {"startTime": "start", "endTime": "end", "venueId": "venue", "seatLimit": "seat_limit"}


@organizer_only
def dashboard(request: HttpRequest) -> HttpResponse:
    return render(request, "web/organizer/dashboard.html", analytics_service.dashboard())


# --- Events ------------------------------------------------------------------
@organizer_only
def events(request: HttpRequest) -> HttpResponse:
    return render(request, "web/organizer/events.html", {"events": event_service.list_all_events()})


def _event_input(form: EventForm, tiers: TierFormSet) -> EventInput:
    d = form.cleaned_data
    tier_rows = [
        TierInput(t["tier_id"], t["name"].strip(), t["section"].strip(), t["price"], t["capacity"])
        for t in tiers.cleaned_data if t and not t.get("DELETE")
    ]
    return EventInput(
        title=d["title"], description=d["description"], type=d["type"], venue_id=ObjectId(d["venue"]),
        start=d["start"], end=d["end"], tags=split_list(d["tags"]),
        artist=d["artist"].strip() or None, tiers=tier_rows,
        instructor=d["instructor"].strip() or None, prerequisites=split_list(d["prerequisites"], "\n"),
        seat_limit=d["seat_limit"],
    )


def _save_event(request: HttpRequest, form: EventForm, tiers: TierFormSet, event_id: ObjectId | None) -> bool:
    """Create or update; on failure attach the error to the form and return False."""
    try:
        data = _event_input(form, tiers)
        if event_id is None:
            event = event_service.create_event(data)
        else:
            event = event_service.update_event(event_id, data)
    except (ValidationFailed, DuplicateError) as exc:
        apply_db_errors(form, exc, EVENT_ALIASES)
        return False
    except FestFlowError as exc:
        form.add_error(None, str(exc))
        return False
    messages.success(request, f"Saved “{event['title']}”.")
    return True


@organizer_only
def event_create(request: HttpRequest) -> HttpResponse:
    venues = event_service.list_venues()
    form = EventForm(request.POST or None, venues=venues)
    tiers = TierFormSet(request.POST or None, prefix="tiers")
    if request.method == "POST" and form.is_valid() and tiers.is_valid():
        if _save_event(request, form, tiers, None):
            return redirect("organizer_events")
    return render(request, "web/organizer/event_form.html",
                  {"form": form, "tiers": tiers, "venues": venues, "editing": False})


@organizer_only
def event_edit(request: HttpRequest, event_id: ObjectId) -> HttpResponse:
    try:
        event = event_service.get_event_detail(event_id)
    except NotFoundError as exc:
        raise Http404(str(exc)) from exc
    venues = event_service.list_venues()
    initial = {
        "title": event["title"], "description": event["description"], "type": event["type"],
        "venue": str(event["venueId"]), "start": event["startTime"], "end": event["endTime"],
        "tags": ", ".join(event["tags"]), "artist": event.get("artist", ""),
        "instructor": event.get("instructor", ""), "prerequisites": "\n".join(event.get("prerequisites", [])),
        "seat_limit": event.get("seatLimit"),
    }
    tier_initial = [{"tier_id": t["tierId"], "name": t["name"], "section": t["section"],
                     "price": t["price"], "capacity": t["capacity"]} for t in event.get("ticketTiers", [])]
    form = EventForm(request.POST or None, venues=venues, editing=True, initial=initial)
    tiers = TierFormSet(request.POST or None, prefix="tiers", initial=tier_initial)
    if request.method == "POST" and form.is_valid() and tiers.is_valid():
        if _save_event(request, form, tiers, event_id):
            return redirect("organizer_events")
    return render(request, "web/organizer/event_form.html",
                  {"form": form, "tiers": tiers, "venues": venues, "editing": True, "event": event})


@require_POST
@organizer_only
def event_cancel(request: HttpRequest, event_id: ObjectId) -> HttpResponse:
    try:
        refunded = event_service.cancel_event(event_id)
    except FestFlowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Event cancelled; {refunded} booking(s) refunded or cancelled.")
    return redirect("organizer_events")


@require_POST
@organizer_only
def event_delete(request: HttpRequest, event_id: ObjectId) -> HttpResponse:
    try:
        event_service.delete_event(event_id)
    except FestFlowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Event deleted.")
    return redirect("organizer_events")


# --- Venues ------------------------------------------------------------------
@organizer_only
def venues(request: HttpRequest) -> HttpResponse:
    return render(request, "web/organizer/venues.html", {"venues": event_service.list_venues()})


def _venue_input(form: VenueForm) -> VenueInput:
    d = form.cleaned_data
    return VenueInput(d["name"], d["block"], d["sections"], d["facilities"])


@organizer_only
def venue_form(request: HttpRequest, venue_id: ObjectId | None = None) -> HttpResponse:
    initial: dict[str, Any] = {}
    if venue_id is not None:
        venue = next((v for v in event_service.list_venues() if v["_id"] == venue_id), None)
        if venue is None:
            raise Http404("Venue not found.")
        initial = {"name": venue["name"], "block": venue["block"], "facilities": venue["facilities"],
                   "sections": "\n".join(f"{s['name']}: {s['capacity']}" for s in venue["sections"])}
    form = VenueForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            if venue_id is None:
                event_service.create_venue(_venue_input(form))
            else:
                event_service.update_venue(venue_id, _venue_input(form))
        except (ValidationFailed, DuplicateError) as exc:
            apply_db_errors(form, exc)
        except FestFlowError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Venue saved.")
            return redirect("organizer_venues")
    return render(request, "web/organizer/venue_form.html", {"form": form, "editing": venue_id is not None})


@require_POST
@organizer_only
def venue_delete(request: HttpRequest, venue_id: ObjectId) -> HttpResponse:
    try:
        event_service.delete_venue(venue_id)
    except FestFlowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Venue deleted.")
    return redirect("organizer_venues")


# --- Check-in ----------------------------------------------------------------
@organizer_only
def checkin(request: HttpRequest) -> HttpResponse:
    result = None
    form = CheckInForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        result = checkin_service.check_in(form.cleaned_data["code"])
        form = CheckInForm()  # empty box, ready for the next scan
    return render(request, "web/organizer/checkin.html", {"form": form, "result": result})


# --- Volunteers --------------------------------------------------------------
@organizer_only
def volunteers(request: HttpRequest) -> HttpResponse:
    people = volunteer_service.list_volunteers()
    open_events = [e for e in event_service.list_all_events() if e["status"] != "cancelled"]
    form = ShiftForm(request.POST or None, volunteers=people, events=open_events)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            volunteer_service.assign_shift(ObjectId(d["volunteer"]), ObjectId(d["event"]), d["duty"],
                                           d["start"], d["end"])
        except ValidationFailed as exc:
            apply_db_errors(form, exc)
        except FestFlowError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Shift assigned.")
            return redirect("organizer_volunteers")
    overview = analytics_service.volunteer_overview()
    hours = {h["userId"]: h["hours"] for h in overview["hours"]}
    for person in people:
        person["hours"] = hours.get(person["id"], 0)
    return render(request, "web/organizer/volunteers.html", {
        "form": form, "volunteers": people, "coverage": overview["coverage"],
        "duties": ["gate", "stage", "help_desk", "logistics"],
    })


@require_POST
@organizer_only
def shift_remove(request: HttpRequest, user_id: ObjectId) -> HttpResponse:
    try:
        volunteer_service.remove_shift(user_id, ObjectId(request.POST["event_id"]),
                                       datetime.fromisoformat(request.POST["start"]))
    except (FestFlowError, KeyError, ValueError) as exc:
        messages.error(request, f"Could not remove the shift: {exc}")
    else:
        messages.success(request, "Shift removed.")
    return redirect("organizer_volunteers")
