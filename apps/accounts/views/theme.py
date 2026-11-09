"""The account page's "Appearance" form."""

import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.views.decorators.http import require_POST

from apps.core import color_schemes
from apps.core.htmx import is_htmx

from ..forms import ThemeForm
from ..http import AuthenticatedHttpRequest
from ..services.theme import set_color_scheme, set_theme
from .pages import account_page_context

#: The htmx event a successful save fires (`HX-Trigger`). `core/base.html` listens for it by this
#: name and applies `detail.theme` to the page; `core` doesn't know about accounts.
THEME_CHANGED_EVENT = 'themeChanged'


@login_required
@require_POST
def theme_update(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Save the theme, and the colour scheme when one is posted, picked on the account page.

    For htmx, a save answers 200 with the form re-rendered (the new choice checked), but tells htmx
    not to swap it in (`HX-Reswap: none`): replacing the radios would drop the keyboard focus from
    the one the user is on. Instead `HX-Trigger` fires a `themeChanged` event carrying the theme
    and the account's colour scheme (its id, logo URLs and browser chrome colour), and
    `core/base.html` applies both to the page at once, with no reload. Without htmx, redirects
    back to the account page.

    An invalid value is a 422 and nothing is saved: the partial for htmx (swapped in, since it
    carries the error), the whole page otherwise.
    """
    form = ThemeForm(request.POST)
    if form.is_valid():
        theme = form.cleaned_data['theme']
        set_theme(request.user, theme)
        if form.cleaned_data['color_scheme']:
            set_color_scheme(request.user, form.cleaned_data['color_scheme'])
        if not is_htmx(request):
            return redirect('accounts:account')
        response = render(request, 'accounts/partials/theme_form.html')
        response['HX-Reswap'] = 'none'
        scheme = color_schemes.scheme_for(request.user)
        detail = {
            'theme': theme,
            'scheme': {
                'id': scheme.id,
                'theme_color': scheme.theme_color,
                'logos': {
                    str(size): static(scheme.logo_path(size)) for size in color_schemes.LOGO_SIZES
                },
            },
        }
        response['HX-Trigger'] = json.dumps({THEME_CHANGED_EVENT: detail})
        return response

    if is_htmx(request):
        context = {'theme_form': form}
        return render(request, 'accounts/partials/theme_form.html', context, status=422)
    context = account_page_context(request, theme_form=form)
    return render(request, 'accounts/account.html', context, status=422)
