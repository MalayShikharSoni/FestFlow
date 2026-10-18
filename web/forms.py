"""
Plain forms.Form classes (no ModelForm — there are no Django models).

Forms only PARSE input (strings -> ints, datetimes, lists). Shape rules (lengths,
patterns, enums, end > start, ...) are deliberately not repeated here: the
MongoDB validator enforces them, and web/form_errors.py puts its complaints back
onto the matching form field.
"""
from datetime import datetime, timezone
from typing import Any

from django import forms

from core.schemas import DUTIES, EVENT_TYPES, FACILITIES
from services.booking_service import MAX_TICKETS_PER_BOOKING, PAID_MODES

DATETIME_LOCAL = "%Y-%m-%dT%H:%M"
EVENT_TYPE_CHOICES = [("pro_show", "Pro show"), ("cultural", "Cultural"), ("workshop", "Workshop")]
assert [c for c, _ in EVENT_TYPE_CHOICES] == EVENT_TYPES


class BootstrapMixin:
    """Give every widget the Bootstrap 5 CSS class it needs."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple, forms.RadioSelect)):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "form-select")
            else:
                widget.attrs.setdefault("class", "form-control")


class DateTimeLocalField(forms.DateTimeField):
    """<input type="datetime-local">. The browser sends IST wall-clock time (no zone);
    Django makes it aware in TIME_ZONE (Asia/Kolkata) and we convert to UTC for storage."""

    widget = forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_LOCAL)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(input_formats=[DATETIME_LOCAL], **kwargs)

    def clean(self, value: Any) -> datetime | None:
        result = super().clean(value)
        return result.astimezone(timezone.utc) if result else None


def split_list(text: str, separator: str = ",") -> list[str]:
    return [part.strip() for part in text.replace("\n", separator).split(separator) if part.strip()]


# --- Accounts ----------------------------------------------------------------
class RegisterForm(BootstrapMixin, forms.Form):
    name = forms.CharField(max_length=80)
    email = forms.EmailField()
    phone = forms.CharField(max_length=10, help_text="10-digit Indian mobile number")
    college = forms.CharField(max_length=120, initial="Lovely Professional University")
    reg_no = forms.CharField(label="Registration no.", max_length=8, required=False,
                             help_text="Optional, 8 digits")
    password = forms.CharField(widget=forms.PasswordInput, min_length=8)
    password2 = forms.CharField(label="Repeat password", widget=forms.PasswordInput)

    def clean(self) -> dict[str, Any]:
        data = super().clean()
        if data.get("password") and data.get("password") != data.get("password2"):
            self.add_error("password2", "The passwords do not match.")
        return data


class LoginForm(BootstrapMixin, forms.Form):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)


# --- Booking -----------------------------------------------------------------
class BookingForm(BootstrapMixin, forms.Form):
    """Tier + quantity + payment for ticketed events; workshops need no fields."""

    tier = forms.ChoiceField(widget=forms.RadioSelect)
    qty = forms.IntegerField(label="Tickets", min_value=1, max_value=MAX_TICKETS_PER_BOOKING, initial=1)
    payment_mode = forms.ChoiceField(required=False, choices=[("", "—")] + [(m, m.title()) for m in PAID_MODES],
                                     help_text="Not needed for free tiers")

    def __init__(self, *args: Any, event: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if event["type"] == "workshop":
            for name in ("tier", "qty", "payment_mode"):
                del self.fields[name]
            return
        self.fields["tier"].choices = [
            (t["tierId"], f"{t['name']} · ₹{t['price']} ({t['left']} left)") for t in event["ticketTiers"]
        ]


# --- Events ------------------------------------------------------------------
class EventForm(BootstrapMixin, forms.Form):
    title = forms.CharField(max_length=120)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    type = forms.ChoiceField(choices=EVENT_TYPE_CHOICES)
    venue = forms.ChoiceField()
    start = DateTimeLocalField(label="Starts (IST)")
    end = DateTimeLocalField(label="Ends (IST)")
    tags = forms.CharField(required=False, help_text="Comma-separated")
    artist = forms.CharField(required=False, help_text="Pro shows only")
    instructor = forms.CharField(required=False, help_text="Workshops only")
    prerequisites = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}),
                                    help_text="Workshops only; one per line")
    seat_limit = forms.IntegerField(required=False, help_text="Workshops only (1-200)")

    def __init__(self, *args: Any, venues: list[dict[str, Any]], editing: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["venue"].choices = [(v["id"], f"{v['name']} ({v['block']})") for v in venues]
        if editing:
            # An event's type never changes (bookings copy it as eventType).
            self.fields["type"].disabled = True


class TierForm(BootstrapMixin, forms.Form):
    tier_id = forms.CharField(label="Tier id", max_length=10, widget=forms.TextInput(attrs={"placeholder": "GOLD"}))
    name = forms.CharField(max_length=40, widget=forms.TextInput(attrs={"placeholder": "Gold"}))
    section = forms.CharField(max_length=40, widget=forms.TextInput(attrs={"placeholder": "Venue section"}))
    price = forms.IntegerField(label="Price (₹)", min_value=0)
    capacity = forms.IntegerField(min_value=1)

    def clean_tier_id(self) -> str:
        return self.cleaned_data["tier_id"].strip().upper()


TierFormSet = forms.formset_factory(TierForm, extra=0, can_delete=True)


class VenueForm(BootstrapMixin, forms.Form):
    name = forms.CharField(max_length=80)
    block = forms.CharField(max_length=40)
    sections = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}),
                               help_text="One per line as  Name: capacity  (e.g. Floor: 1200)")
    facilities = forms.MultipleChoiceField(choices=[(f, f.replace("_", " ")) for f in FACILITIES],
                                           widget=forms.CheckboxSelectMultiple, required=False)

    def clean_sections(self) -> list[tuple[str, int]]:
        sections = []
        for line in self.cleaned_data["sections"].splitlines():
            if not line.strip():
                continue
            name, sep, capacity = line.rpartition(":")
            if not sep or not capacity.strip().isdigit():
                raise forms.ValidationError(f"'{line.strip()}' should look like  Name: 300")
            sections.append((name.strip(), int(capacity)))
        return sections


# --- Operations --------------------------------------------------------------
class CheckInForm(BootstrapMixin, forms.Form):
    code = forms.CharField(max_length=20, widget=forms.TextInput(
        attrs={"autofocus": True, "autocomplete": "off", "placeholder": "SPX-XXXXXXXX",
               "class": "form-control form-control-lg text-uppercase"}))


class ShiftForm(BootstrapMixin, forms.Form):
    volunteer = forms.ChoiceField()
    event = forms.ChoiceField()
    duty = forms.ChoiceField(choices=[(d, d.replace("_", " ")) for d in DUTIES])
    start = DateTimeLocalField(required=False, label="From (IST)", help_text="Empty = event start")
    end = DateTimeLocalField(required=False, label="To (IST)", help_text="Empty = event end")

    def __init__(self, *args: Any, volunteers: list[dict[str, Any]], events: list[dict[str, Any]],
                 **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["volunteer"].choices = [(v["id"], v["name"]) for v in volunteers]
        self.fields["event"].choices = [(e["id"], e["title"]) for e in events]


class QueryLabForm(BootstrapMixin, forms.Form):
    collection = forms.ChoiceField(choices=[(c, c) for c in ("bookings", "events", "users", "venues")])
    pipeline = forms.CharField(widget=forms.Textarea(attrs={"rows": 8, "spellcheck": "false",
                                                            "class": "form-control font-monospace small"}))
