"""Register, login, logout — our own session auth against the MongoDB users collection."""
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.auth import login_required_mongo, login_session, logout_session
from core.exceptions import DuplicateError, ValidationFailed
from services import auth_service, volunteer_service
from web.form_errors import apply_db_errors
from web.forms import LoginForm, RegisterForm
from web.views import session_user_id


def register(request: HttpRequest) -> HttpResponse:
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            user = auth_service.register(data["name"], data["email"], data["phone"], data["password"],
                                         data["college"], data["reg_no"] or None)
        except (ValidationFailed, DuplicateError) as exc:
            # The validator (e.g. phone pattern) or the unique email index said no.
            apply_db_errors(form, exc, aliases={"regNo": "reg_no"})
        else:
            login_session(request, str(user["_id"]), user["role"])
            messages.success(request, f"Welcome to Spectra 2026, {user['name']}!")
            return redirect("event_list")
    return render(request, "web/register.html", {"form": form})


def login_view(request: HttpRequest) -> HttpResponse:
    form = LoginForm(request.POST or None)
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if request.method == "POST" and form.is_valid():
        user = auth_service.authenticate(form.cleaned_data["email"], form.cleaned_data["password"])
        if user is None:
            form.add_error(None, "Email or password is incorrect.")
        else:
            login_session(request, str(user["_id"]), user["role"])
            messages.success(request, f"Logged in as {user['name']}.")
            # Only redirect back to our own site (an open redirect would be a phishing aid).
            if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}):
                return redirect(next_url)
            return redirect("dashboard" if user["role"] == "organizer" else "event_list")
    return render(request, "web/login.html", {"form": form, "next": next_url})


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """POST only, so a link or image on another site cannot log you out."""
    logout_session(request)
    messages.info(request, "You have been logged out.")
    return redirect("event_list")


@login_required_mongo
def my_shifts(request: HttpRequest) -> HttpResponse:
    """A volunteer's own schedule (the shifts embedded in their user document)."""
    return render(request, "web/my_shifts.html",
                  {"shifts": volunteer_service.shifts_for(session_user_id(request))})
