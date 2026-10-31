"""Redeeming a promo code on the web (issue #70): the form on the account and credits pages, and
the shareable redeem link.

The form follows the "HTMX form views" convention: `POST /billing/redeem/` answers a redirect with
a flash message on success and the re-rendered partial with 422 on a refusal, the refusal being
`PromoCodeError.message` (specific where harmless, see `apps.billing.services.promo`). Two rate
limits are charged before anything is looked up, so codes can't be guessed through it:
`PROMO_REDEEM_USER` (per user) and `PROMO_REDEEM_IP`.

The redeem link, `GET /billing/redeem/<code>/`, only shows the form with the code filled in (a GET
never spends credits or a rate limit). Without a session it goes to the login page with `next`
pointing back here and `promo=<code>`, which the login page forwards to the register page's
"Create one" link so a new visitor's code survives signing up (`apps.accounts.signup_codes`).
"""

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest, MaybeAuthenticatedHttpRequest
from apps.accounts.signup_codes import QUERY_PARAM
from apps.core import ratelimit
from apps.core.htmx import hx_redirect, is_htmx

from ..forms import RedeemForm
from ..models.promo import CODE_MAX_LENGTH
from ..services import promo

PARTIAL = 'billing/partials/redeem_form.html'


def _next_url(request: HttpRequest, candidate: str | None) -> str:
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return reverse('billing:credits-history')


@require_GET
def redeem_page(request: MaybeAuthenticatedHttpRequest, code: str) -> HttpResponse:
    """The redeem link: the form with `code` filled in, for the user to confirm."""
    code = code.strip()[:CODE_MAX_LENGTH]
    if not request.user.is_authenticated:
        query = urlencode({'next': request.get_full_path(), QUERY_PARAM: code})
        return redirect(f'{reverse("accounts:login")}?{query}')
    context = {
        'promo_code': code.upper(),
        'promo_next': reverse('billing:credits-history'),
    }
    return render(request, 'billing/redeem.html', context)


@login_required
@require_POST
def redeem(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Redeem the posted code for the signed-in user."""
    form = RedeemForm(request.POST)
    next_url = _next_url(request, request.POST.get('next'))
    if not form.is_valid():
        return _refusal(request, form, next_url, 'Enter a code.')

    for rule, limited in (
        ('PROMO_REDEEM_USER', ratelimit.hit_user(request.user, 'PROMO_REDEEM_USER')),
        ('PROMO_REDEEM_IP', ratelimit.hit_ip(request, 'PROMO_REDEEM_IP')),
    ):
        if not limited.allowed:
            return ratelimit.web_response(request, limited, retarget='#promo-msg')

    try:
        redemption = promo.redeem(request.user, form.cleaned_data['code'])
    except promo.PromoCodeError as exc:
        return _refusal(request, form, next_url, exc.message)

    messages.success(
        request,
        f'Added {redemption.credits} credits with promo code {redemption.code.code}.',
    )
    return hx_redirect(request, next_url)


def _refusal(request: HttpRequest, form: RedeemForm, next_url: str, message: str) -> HttpResponse:
    context = {
        'promo_code': form.data.get('code', '')[:CODE_MAX_LENGTH],
        'promo_next': next_url,
        'promo_error': message,
    }
    if is_htmx(request):
        return render(request, PARTIAL, context, status=422)
    # Without htmx, show the refusal on the page the form came from.
    messages.error(request, message)
    return redirect(next_url)
