"""The Gmail forwarding confirmation a user needs to finish setting up auto-forwarding to their
inbox address: the code (and link) Google emails to the address, which the intake stores instead of
treating the message as a receipt. Shown on the settings page and returned by the API's `/inbox`.
"""

from datetime import datetime, timedelta
from typing import TypedDict

from django.utils import timezone

from apps.accounts.models import User

from ..models import InboundEmail, InboundEmailStatus
from .mime import is_forwarding_link

#: How long a forwarding confirmation stays visible (the codes expire at the provider).
CONFIRMATION_SHOWN_FOR = timedelta(days=7)


class ForwardingConfirmation(TypedDict):
    received_at: datetime
    code: str
    url: str


def safe_confirmation_url(url: str) -> str:
    """`url` if it is an https link to Google's mail settings, else an empty string."""
    return url if is_forwarding_link(url) else ''


def forwarding_confirmation(user: User) -> ForwardingConfirmation | None:
    """The latest forwarding confirmation received in the last 7 days, or `None`."""
    email = (
        InboundEmail.objects.filter(
            user=user,
            status=InboundEmailStatus.FORWARDING_CONFIRMATION,
            created_at__gte=timezone.now() - CONFIRMATION_SHOWN_FOR,
        )
        .exclude(forwarding_confirmation_code='', forwarding_confirmation_url='')
        .order_by('-created_at')
        .first()
    )
    if email is None:
        return None
    return {
        'received_at': email.received_at or email.created_at,
        'code': email.forwarding_confirmation_code,
        'url': safe_confirmation_url(email.forwarding_confirmation_url),
    }
