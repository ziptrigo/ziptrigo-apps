"""The inbox endpoints (`/api/receipts/inbox`): the address receipts are emailed to, who may send
to it, the default currency, and the Gmail forwarding confirmation to act on (if any)."""

from apps.core import ratelimit

from .. import services
from ..schemas import InboxSchema, InboxUpdateSchema
from .common import mapped_errors
from .router import router


def _inbox(user) -> dict:
    account = services.get_account(user)
    return {
        'address': services.inbox_address(services.active_inbox_code(account).code),
        'sender_allowlist': list(account.sender_allowlist),
        'default_currency': account.default_currency,
        'forwarding_confirmation': services.forwarding_confirmation(user),
    }


@router.get('/inbox', response=InboxSchema)
def get_inbox(request):
    """The inbox address, the accepted senders (an empty list accepts anyone), the default
    currency and the latest forwarding confirmation of the last 7 days (or `null`)."""
    return _inbox(request.auth)


@router.patch('/inbox', response=InboxSchema)
def update_inbox(request, payload: InboxUpdateSchema):
    """Change the accepted senders and/or the default currency; only the fields present in the
    body are applied (send `"sender_allowlist": []` to accept anyone)."""
    with mapped_errors():
        services.update_inbox_settings(request.auth, **payload.dict(exclude_unset=True))
    return _inbox(request.auth)


@router.post('/inbox/regenerate', response=InboxSchema)
def regenerate_inbox(request):
    """Retire the current address immediately and issue a new one. Mail still sent to the old
    address is dropped."""
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'RECEIPTS_REGENERATE_USER'))
    services.regenerate_inbox_code(request.auth)
    return _inbox(request.auth)
