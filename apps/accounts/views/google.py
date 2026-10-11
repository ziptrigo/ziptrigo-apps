"""Sign in with Google (issue #91): `GET /auth/google/login` sends the browser to Google and
`GET /auth/google/callback` finishes the sign-in. See `apps.accounts.services.google_oauth` for
the flow, the account matching and why it ends in a Django session rather than a JWT.

Both are plain full-page navigations (no htmx), GET-only and open to anonymous visitors. Authlib
owns the `state` (CSRF) and `nonce` handling: it stores them in the session on the way out and
refuses a callback that doesn't carry back a state it issued. Whatever goes wrong -- Google
reporting an error, a bad state, a failed token exchange -- the visitor is sent back to the login
page with a friendly message; the details are logged, never the codes, tokens or secrets, and
never shown.
"""

import logging

from authlib.integrations.django_client import OAuthError
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.sessions.backends.base import SessionBase
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET

from apps.core import ratelimit

from ..services import google_oauth
from ..services.google_oauth import GoogleNotConfigured, GoogleSignInError, UnverifiedEmail
from .login import _redirect_target

logger = logging.getLogger(__name__)

#: Session key holding the same-site `next` URL across the round trip to Google.
NEXT_SESSION_KEY = 'google_oauth_next'

CANCELLED_MESSAGE = (
    'Google sign-in was cancelled. You can try again, or sign in with your email and password.'
)
FAILED_MESSAGE = "We couldn't sign you in with Google. Please try again."


class _SessionRequest(HttpRequest):
    """`HttpRequest` typed with the `.session` attribute `SessionMiddleware` adds at runtime."""

    session: SessionBase


def _to_login(request: HttpRequest, message: str) -> HttpResponse:
    messages.error(request, message)
    return redirect('accounts:login')


def _unavailable(request: HttpRequest) -> HttpResponse:
    logger.error(
        'Google sign-in requested but GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and '
        'GOOGLE_REDIRECT_URI are not all set'
    )
    return _to_login(request, GoogleNotConfigured.user_message)


@require_GET
def google_login(request: _SessionRequest) -> HttpResponse:
    """Redirect to Google's consent screen.

    A same-site `?next=` (the same rule the password login applies) is remembered in the session
    and honoured after the callback.
    """
    if not google_oauth.is_configured():
        return _unavailable(request)

    next_url = request.GET.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        request.session[NEXT_SESSION_KEY] = next_url
    else:
        request.session.pop(NEXT_SESSION_KEY, None)

    try:
        return google_oauth.get_client().authorize_redirect(
            request, redirect_uri=settings.GOOGLE_REDIRECT_URI
        )
    except Exception:
        # Typically Google's discovery document being unreachable; nothing the visitor can fix.
        logger.exception('Could not start the Google sign-in redirect')
        return _to_login(request, FAILED_MESSAGE)


@require_GET
def google_callback(request: _SessionRequest) -> HttpResponse:
    """Finish the sign-in: exchange the code, read the ID token's claims, start the session."""
    limited = ratelimit.hit_ip(request, 'GOOGLE_LOGIN_IP')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)

    # Popped first, so it never outlives this attempt however the attempt ends.
    next_url = request.session.pop(NEXT_SESSION_KEY, None)

    if not google_oauth.is_configured():
        return _unavailable(request)

    try:
        token = google_oauth.get_client().authorize_access_token(request)
        # Authlib validated the ID token (signature, issuer, audience, expiry, nonce) and put its
        # claims here; a response without them is not something we can sign anyone in with.
        identity = google_oauth.identity_from_claims(token.get('userinfo') or {})
    except OAuthError as exc:
        # Consent denied (`?error=access_denied`), a missing/mismatching `state`, a rejected code,
        # a failed ID token validation... `error` is a short code and `description` Google's own
        # text; neither holds the authorization code or any token.
        logger.warning(
            'Google sign-in failed: %s error=%r description=%r',
            type(exc).__name__,
            str(exc.error)[:100],
            str(exc.description)[:200],
        )
        denied = request.GET.get('error') == 'access_denied'
        return _to_login(request, CANCELLED_MESSAGE if denied else FAILED_MESSAGE)
    except GoogleSignInError as exc:
        logger.warning('Google sign-in failed: %s', type(exc).__name__)
        return _to_login(request, exc.user_message)
    except Exception:
        logger.exception('Google sign-in failed unexpectedly')
        return _to_login(request, FAILED_MESSAGE)

    try:
        user = google_oauth.resolve_user(identity)
    except UnverifiedEmail:
        logger.warning("Google sign-in refused: the Google account's email is not verified")
        return render(
            request,
            'accounts/google_error.html',
            {'message': UnverifiedEmail.user_message},
            status=403,
        )
    except GoogleSignInError as exc:
        logger.warning('Google sign-in refused: %s', type(exc).__name__)
        return _to_login(request, exc.user_message)

    # The same call the password form makes, so `user_logged_in` fires too (last login, and
    # `file_transfer` claiming a confirmed anonymous transfer for this address).
    auth_login(request, user, backend=google_oauth.LOGIN_BACKEND)
    return redirect(_redirect_target(request, next_url))
