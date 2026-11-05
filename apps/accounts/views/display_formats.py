"""The account page's date and time format form (issue #245)."""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.core.htmx import is_htmx

from ..forms import DisplayFormatsForm
from ..http import AuthenticatedHttpRequest
from ..services.display_formats import set_display_formats
from .pages import account_page_context


@login_required
@require_POST
def formats_update(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Save the date order and/or the clock picked on the account page.

    Shaped like the theme view: for htmx a save answers 200 with the form re-rendered but tells
    htmx not to swap it in (`HX-Reswap: none`), so the keyboard focus stays on the radio; without
    htmx it redirects back to the account page. Other pages show the new format when they next
    load. An invalid value, or a post with neither field, is a 422 and nothing is saved: the
    partial for htmx, the whole page otherwise.
    """
    form = DisplayFormatsForm(request.POST)
    if form.is_valid():
        set_display_formats(
            request.user,
            date_format=form.cleaned_data['date_format'] or None,
            time_format=form.cleaned_data['time_format'] or None,
        )
        if not is_htmx(request):
            return redirect('accounts:account')
        response = render(request, 'accounts/partials/display_formats_form.html')
        response['HX-Reswap'] = 'none'
        return response

    if is_htmx(request):
        context = {'formats_form': form}
        return render(request, 'accounts/partials/display_formats_form.html', context, status=422)
    context = account_page_context(request, formats_form=form)
    return render(request, 'accounts/account.html', context, status=422)
