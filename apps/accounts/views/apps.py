"""The account page's "Apps" form (issue #246)."""

from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.core.htmx import is_htmx
from apps.core.products import products_for

from ..forms import AppsForm
from ..http import AuthenticatedHttpRequest
from ..services.apps import set_app_preferences
from .pages import account_page_context


@login_required
@require_POST
def apps_update(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Save which apps the user sees and their order.

    For htmx, a save answers 200 with `HX-Reswap: none` (the tiles stay as they are, so the
    keyboard focus on the control the user is on isn't lost) and the header's product links out of
    band, so the menu follows at once with no reload. Without htmx, redirects back to the account
    page. An unknown label is a 422 and nothing is saved: the partial for htmx (swapped in, since
    it carries the error), the whole page otherwise.
    """
    form = AppsForm(request.POST)
    if form.is_valid():
        set_app_preferences(request.user, form.cleaned_data['order'], form.cleaned_data['enabled'])
        if not is_htmx(request):
            return redirect('accounts:account')
        response = render(request, 'core/partials/product_nav.html', {'oob': True})
        response['HX-Reswap'] = 'none'
        return response

    if is_htmx(request):
        context: dict[str, Any] = {'apps_form': form, 'app_rows': products_for(request.user)}
        return render(request, 'accounts/partials/apps_form.html', context, status=422)
    context = account_page_context(request, apps_form=form)
    return render(request, 'accounts/account.html', context, status=422)


@login_required
@require_GET
def apps_form_partial(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The Apps form as stored, as a partial.

    The page fetches it after a save failed (`apps_order.js`), so the tiles show what the server
    has again; `?failed=1` makes it carry the "Couldn't save" line.
    """
    context: dict[str, Any] = {
        'app_rows': products_for(request.user),
        'save_failed': request.GET.get('failed') == '1',
    }
    return render(request, 'accounts/partials/apps_form.html', context)
