from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Infrastructure: Mongo client, schemas, indexes, auth, management commands."""

    name = "core"
