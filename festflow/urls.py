"""Root URL configuration."""
from django.urls import include, path, register_converter

from core.converters import ObjectIdConverter

# Lets routes say <oid:event_id>; the view receives a real bson.ObjectId.
register_converter(ObjectIdConverter, "oid")

urlpatterns = [
    path("", include("web.urls")),
]
