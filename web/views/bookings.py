"""Booking, my bookings, ticket page with QR codes, cancellation."""
from bson import ObjectId
from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core.auth import login_required_mongo
from core.exceptions import FestFlowError, NotFoundError, SoldOutError, ValidationFailed
from services import booking_service, event_service
from web.form_errors import problems_text
from web.forms import BookingForm
from web.qr import qr_data_uri
from web.views import session_user_id


@login_required_mongo
def book(request: HttpRequest, event_id: ObjectId) -> HttpResponse:
    try:
        event = event_service.get_event_detail(event_id)
    except NotFoundError as exc:
        raise Http404(str(exc)) from exc
    # Not `request.POST or None`: a workshop booking has no fields, so its POST can be
    # empty — and an empty QueryDict is falsy, which would leave the form unbound.
    form = BookingForm(request.POST if request.method == "POST" else None, event=event)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            booking = booking_service.book(session_user_id(request), event_id, data.get("tier"),
                                           data.get("qty", 1), data.get("payment_mode", ""))
        except SoldOutError as exc:
            form.add_error(None, str(exc))
        except ValidationFailed as exc:
            form.add_error(None, f"The database rejected this booking: {problems_text(exc)}")
        except FestFlowError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Booked! Show these QR codes at the gate.")
            return redirect("ticket", booking_id=booking["_id"])
    return render(request, "web/event_detail.html", {"event": event, "form": form})


@login_required_mongo
def my_bookings(request: HttpRequest) -> HttpResponse:
    return render(request, "web/my_bookings.html",
                  {"bookings": booking_service.my_bookings(session_user_id(request))})


@login_required_mongo
def ticket(request: HttpRequest, booking_id: ObjectId) -> HttpResponse:
    try:
        booking = booking_service.booking_for_user(session_user_id(request), booking_id)
    except NotFoundError as exc:
        raise Http404(str(exc)) from exc
    for t in booking["tickets"]:
        t["qr"] = qr_data_uri(t["code"])
    return render(request, "web/ticket.html", {"booking": booking})


@require_POST
@login_required_mongo
def cancel_booking(request: HttpRequest, booking_id: ObjectId) -> HttpResponse:
    try:
        booking = booking_service.cancel(session_user_id(request), booking_id)
    except FestFlowError as exc:
        messages.error(request, str(exc))
    else:
        refund = " A refund is on its way." if booking["status"] == "refunded" else ""
        messages.success(request, f"Booking cancelled.{refund}")
    return redirect("ticket", booking_id=booking_id)
