"""The account page's "Appearance" form."""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.core.htmx import is_htmx

from ..forms import ChangePasswordForm, ProfileForm, ThemeForm
from ..http import AuthenticatedHttpRequest
from ..services.theme import set_theme


@login_required
@require_POST
def theme_update(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Save the theme picked on the account page.

    On success htmx is told to refresh the page (`HX-Refresh`): the Tailwind CDN builds the
    `dark:` styles when the page loads, so the new theme only shows after a reload, and a refresh
    (unlike a redirect) keeps the scroll position on the section that was just changed. Without
    htmx, redirects back to the account page. An invalid value is a 422: the partial for htmx,
    the whole page otherwise, and nothing is saved.
    """
    form = ThemeForm(request.POST)
    if form.is_valid():
        set_theme(request.user, form.cleaned_data['theme'])
        if is_htmx(request):
            response = HttpResponse()
            response['HX-Refresh'] = 'true'
            return response
        return redirect('accounts:account')

    if is_htmx(request):
        context = {'theme_form': form}
        return render(request, 'accounts/partials/theme_form.html', context, status=422)
    context = {
        'form': ProfileForm(instance=request.user),
        'password_form': ChangePasswordForm(request.user),
        'theme_form': form,
    }
    return render(request, 'accounts/account.html', context, status=422)
