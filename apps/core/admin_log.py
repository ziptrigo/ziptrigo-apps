"""Admin history (`LogEntry`) for what admin *actions* do (issue #152).

Django logs a change only when it goes through a change form. A bulk action, or a two-step
confirmation page like a takedown, writes through a service instead, so without these calls it
leaves no trace in an object's History. Each call is one row per object, with a sentence for
the change message.
"""

from collections.abc import Iterable

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.db.models import Model
from django.http import HttpRequest


def log_change(request: HttpRequest, objects: Iterable[Model], message: str) -> None:
    """Record that the requesting staff user changed each of ``objects``."""
    _log(request, objects, CHANGE, message)


def log_addition(request: HttpRequest, objects: Iterable[Model], message: str) -> None:
    """Record that the requesting staff user added each of ``objects``."""
    _log(request, objects, ADDITION, message)


def _log(request: HttpRequest, objects: Iterable[Model], flag: int, message: str) -> None:
    objects = list(objects)
    if not objects:
        return
    LogEntry.objects.log_actions(
        # Only admin views call this, behind the admin's own login: `request.user` is set.
        user_id=request.user.pk,  # ty: ignore[unresolved-attribute]
        queryset=objects,
        action_flag=flag,
        change_message=message,
    )
