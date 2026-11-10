"""The one-time accept page (issue #96): where `TermsAcceptanceMiddleware` sends a signed-in user
who has not accepted the current Terms of Use and Privacy Policy."""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from ..forms import AcceptTermsForm
from ..http import AuthenticatedHttpRequest
from ..services import terms
from .redirects import redirect_target


@login_required
@require_http_methods(['GET', 'POST'])
def accept_terms_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Show the checkbox, and record the acceptance with it.

    A plain form: the POST ends in a redirect to the same-site `next` the gate captured (else the
    home page), a missing tick re-renders the page with status 422. A user who has nothing to
    accept is sent straight on, so the URL is harmless to open.
    """
    next_url = request.POST.get('next') or request.GET.get('next')
    target = redirect_target(request, next_url)
    if not terms.needs_acceptance(request.user):
        return redirect(target)

    status = 200
    if request.method == 'POST':
        form = AcceptTermsForm(request.POST)
        if form.is_valid():
            terms.accept(request.user)
            return redirect(target)
        status = 422
    else:
        form = AcceptTermsForm()

    context = {
        'form': form,
        'next': next_url or '',
        # Someone who accepted an earlier version is told the documents changed.
        'is_update': bool(request.user.terms_version),
    }
    return render(request, 'accounts/accept_terms.html', context, status=status)
