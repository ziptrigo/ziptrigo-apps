import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.core.htmx import is_htmx

from ..forms import ProfileForm
from ..http import AuthenticatedHttpRequest

#: The htmx event a successful save fires (`HX-Trigger`), carrying the saved `timezone`.
#: `accounts/js/timezone.js` listens for it to keep `<html data-user-timezone>` current.
PROFILE_SAVED_EVENT = 'profileSaved'


@login_required
@require_POST
def profile_update(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Save the profile form on the account page.

    For htmx, answers with the re-rendered form (status 422 when invalid). Without htmx, redirects
    back to the account page, or re-renders it with the errors.
    """
    form = ProfileForm(request.POST, instance=request.user)

    if form.is_valid():
        form.save()
        if not is_htmx(request):
            return redirect('accounts:account')
        context = {'form': ProfileForm(instance=request.user), 'saved': True}
        response = render(request, 'accounts/partials/profile_form.html', context)
        response['HX-Trigger'] = json.dumps(
            {PROFILE_SAVED_EVENT: {'timezone': request.user.timezone}}
        )
        return response

    template = (
        'accounts/partials/profile_form.html' if is_htmx(request) else 'accounts/account.html'
    )
    return render(request, template, {'form': form}, status=422)
