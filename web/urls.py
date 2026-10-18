from django.urls import path

from web.views import accounts, bookings, organizer, public, query_lab

urlpatterns = [
    # Public / attendee
    path("", public.event_list, name="event_list"),
    path("events/<oid:event_id>/", public.event_detail, name="event_detail"),
    path("events/<oid:event_id>/book/", bookings.book, name="book"),
    path("my/bookings/", bookings.my_bookings, name="my_bookings"),
    path("bookings/<oid:booking_id>/", bookings.ticket, name="ticket"),
    path("bookings/<oid:booking_id>/cancel/", bookings.cancel_booking, name="cancel_booking"),
    path("my/shifts/", accounts.my_shifts, name="my_shifts"),
    path("register/", accounts.register, name="register"),
    path("login/", accounts.login_view, name="login"),
    path("logout/", accounts.logout_view, name="logout"),
    path("health/", public.health, name="health"),
    # Organizer
    path("organizer/", organizer.dashboard, name="dashboard"),
    path("organizer/events/", organizer.events, name="organizer_events"),
    path("organizer/events/new/", organizer.event_create, name="event_create"),
    path("organizer/events/<oid:event_id>/edit/", organizer.event_edit, name="event_edit"),
    path("organizer/events/<oid:event_id>/cancel/", organizer.event_cancel, name="event_cancel"),
    path("organizer/events/<oid:event_id>/delete/", organizer.event_delete, name="event_delete"),
    path("organizer/venues/", organizer.venues, name="organizer_venues"),
    path("organizer/venues/new/", organizer.venue_form, name="venue_create"),
    path("organizer/venues/<oid:venue_id>/edit/", organizer.venue_form, name="venue_edit"),
    path("organizer/venues/<oid:venue_id>/delete/", organizer.venue_delete, name="venue_delete"),
    path("organizer/checkin/", organizer.checkin, name="checkin"),
    path("organizer/volunteers/", organizer.volunteers, name="organizer_volunteers"),
    path("organizer/volunteers/<oid:user_id>/remove-shift/", organizer.shift_remove, name="shift_remove"),
    path("organizer/query-lab/", query_lab.query_lab, name="query_lab"),
]
