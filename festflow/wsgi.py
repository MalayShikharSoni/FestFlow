"""WSGI entry point (used by runserver and any production WSGI server)."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "festflow.settings")

application = get_wsgi_application()
