"""
Views, split by area. Rule: views call services (never repositories or PyMongo),
and catch FestFlowError subclasses to show friendly messages.
"""
from bson import ObjectId
from django.http import HttpRequest

from core.auth import get_session_user


def session_user_id(request: HttpRequest) -> ObjectId:
    """The logged-in user's id (views using this are behind @login_required_mongo)."""
    return ObjectId(get_session_user(request)["id"])
