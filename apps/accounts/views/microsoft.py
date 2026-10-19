"""Sign in with Microsoft (issue #156): `GET /auth/microsoft/login` sends the browser to Microsoft
and `GET /auth/microsoft/callback` finishes the sign-in. See `apps.accounts.services.
microsoft_oauth` for the flow, the account matching and why it ends in a Django session rather
than a JWT. It mirrors `views.google`: plain full-page navigations, GET-only, open to anonymous
visitors, Authlib owning the `state` and `nonce`, and every failure ending on the login page with
a friendly message while the details are logged (never codes, tokens or secrets).
"""

import logging

from authlib.integrations.django_client import OAuthError
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET

from apps.core import ratelimit

from ..http import MaybeAuthenticatedHttpRequest
from ..services import microsoft_oauth
from ..services.microsoft_oauth import MicrosoftNotConfigured, MicrosoftSignInError
from .redirects import redirect_target

logger = logging.getLogger(__name__)

#: Session key holding the same-site `next` URL across the round trip to Microsoft.
NEXT_SESSION_KEY = 'microsoft_oauth_next'

CANCELLED_MESSAGE = (
    'Microsoft sign-in was cancelled. You can try again, or sign in with your email and password.'
)
FAILED_MESSAGE = "We couldn't sign you in with Microsoft. Please try again."


def _to_login(request: HttpRequest, message: str) -> HttpResponse:
    messages.error(request, message)
    return redirect('accounts:login')


def _unavailable(request: HttpRequest) -> HttpResponse:
    # Info, not error: anonymous traffic reaches these routes whenever the feature is off, which
    # is the normal state of an environment without a Microsoft app. A *partial* configuration is
    # flagged at startup by the `accounts.W004` system check.
    logger.info(
        'Microsoft sign-in requested but MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET and '
        'MICROSOFT_REDIRECT_URI are not all set'
    )
    return _to_login(request, MicrosoftNotConfigured.user_message)


@require_GET
def microsoft_login(request: MaybeAuthenticatedHttpRequest) -> HttpResponse:
    """Redirect to Microsoft's sign-in page.

    A same-site `?next=` (the same rule the password login applies) is remembered in the session
    and honoured after the callback. `prompt=select_account` always shows the account picker, so
    a browser signed in to several Microsoft accounts doesn't silently pick one.
    """
    if not microsoft_oauth.is_configured():
        return _unavailable(request)

    next_url = request.GET.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        request.session[NEXT_SESSION_KEY] = next_url
    else:
        request.session.pop(NEXT_SESSION_KEY, None)

    try:
        return microsoft_oauth.get_client().authorize_redirect(
            request, redirect_uri=settings.MICROSOFT_REDIRECT_URI, prompt='select_account'
        )
    except Exception:
        # Typically the discovery document being unreachable; nothing the visitor can fix.
        logger.exception('Could not start the Microsoft sign-in redirect')
        return _to_login(request, FAILED_MESSAGE)


@require_GET
def microsoft_callback(request: MaybeAuthenticatedHttpRequest) -> HttpResponse:
    """Finish the sign-in: exchange the code, read the ID token's claims, start the session."""
    limited = ratelimit.hit_ip(request, 'MICROSOFT_LOGIN_IP')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)

    # Popped first, so it never outlives this attempt however the attempt ends.
    next_url = request.session.pop(NEXT_SESSION_KEY, None)

    if not microsoft_oauth.is_configured():
        return _unavailable(request)

    try:
        token = microsoft_oauth.get_client().authorize_access_token(
            request, claims_options=microsoft_oauth.CLAIMS_OPTIONS
        )
        # Authlib validated the ID token (signature, audience, expiry, nonce) and put its claims
        # here; a response without them is not something we can sign anyone in with.
        identity = microsoft_oauth.identity_from_claims(token.get('userinfo') or {})
    except OAuthError as exc:
        # Consent denied (`?error=access_denied`), a missing/mismatching `state`, a rejected code,
        # a failed ID token validation... `error` is a short code and `description` Microsoft's
        # own text; neither holds the authorization code or any token.
        logger.warning(
            'Microsoft sign-in failed: %s error=%r description=%r',
            type(exc).__name__,
            str(exc.error)[:100],
            str(exc.description)[:200],
        )
        denied = request.GET.get('error') == 'access_denied'
        return _to_login(request, CANCELLED_MESSAGE if denied else FAILED_MESSAGE)
    except MicrosoftSignInError as exc:
        logger.warning('Microsoft sign-in failed: %s', type(exc).__name__)
        return _to_login(request, exc.user_message)
    except Exception:
        logger.exception('Microsoft sign-in failed unexpectedly')
        return _to_login(request, FAILED_MESSAGE)

    try:
        user = microsoft_oauth.resolve_user(identity)
    except MicrosoftSignInError as exc:
        logger.warning('Microsoft sign-in refused: %s', type(exc).__name__)
        return _to_login(request, exc.user_message)

    # The same call the password form makes, so `user_logged_in` fires too (last login, and
    # `file_transfer` claiming a confirmed anonymous transfer for this address).
    auth_login(request, user, backend=microsoft_oauth.LOGIN_BACKEND)
    return redirect(redirect_target(request, next_url))
