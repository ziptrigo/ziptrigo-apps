from ninja import Router
from ninja.errors import HttpError

from apps.core import ratelimit

from ..auth import JWTAuth
from ..models import User
from ..schemas import AccountUpdateRequest, AccountUpdateResponse
from ..services.email_change import notify_previous_address
from ..services.email_confirmation import get_email_confirmation_service

router = Router()
jwt_auth = JWTAuth()


@router.put('/account', response=AccountUpdateResponse, auth=jwt_auth)
def update_account(request, payload: AccountUpdateRequest):
    """Update current user's account information (name and/or email).

    Requires JWT authentication.
    """
    # `request` is left unannotated (as elsewhere in this package, e.g. `routers/auth.py`)
    # rather than typed `HttpRequest`, which doesn't declare `.auth` -- `JWTAuth` (see `jwt_auth`
    # above) sets it to the authenticated `User` at runtime.
    user: User = request.auth

    # Update name if provided
    if payload.name is not None:
        user.name = payload.name

    # Update email if provided and different. The new address must be confirmed again, so a user
    # can't claim an address they don't own.
    email_changed = bool(payload.email and payload.email != user.email)
    previous_email = user.email
    previous_email_confirmed = user.email_confirmed
    if email_changed:
        # Charged before the uniqueness check below, which says whether an address has an account
        # (issue #125): a logged-in caller can ask it only so many times. Kept as a 400 rather
        # than made indistinguishable -- see CLAUDE.md's Auth section for why that was accepted.
        # A 429 refuses the whole request on purpose: a name sent along with the email is not
        # saved either (nothing has been written yet), rather than half-applying an update.
        ratelimit.enforce(ratelimit.hit_user(user, 'ACCOUNT_EMAIL_CHANGE_USER'))
        # Check if email already exists
        if User.objects.filter(email=payload.email).exclude(id=user.id).exists():
            raise HttpError(400, 'Email already in use')
        user.email = payload.email
        user.email_confirmed = False
        user.email_confirmed_at = None

    user.save()

    if email_changed:
        service = get_email_confirmation_service()
        # Invalidate any still-pending confirmation for the address being left behind first
        # (issue #58 follow-up), so a link already sent to it can't later confirm whatever
        # account claims that address next.
        service.invalidate_pending_for_email(previous_email)
        service.send_confirmation_email(user)
        # Tell the address being left behind, but only one the owner had proven: an unconfirmed
        # one may never have been theirs.
        if previous_email_confirmed:
            notify_previous_address(user, previous_email)

    return AccountUpdateResponse(
        message=(
            # Shown even if the cooldown silently skipped the send (`send_confirmation_email`
            # swallows `ResendTooSoon`) or every backend failed to deliver it
            # (`EmailVerificationSendFailed`) -- matches the old JWT-based flow, which never
            # surfaced a send failure here either (see `send_confirmation_email`'s docstring).
            'Profile updated. Check your inbox to confirm your new email address.'
            if email_changed
            else 'Profile updated successfully'
        ),
        user={
            'id': str(user.id),
            'name': user.name,
            'email': user.email,
        },
    )
