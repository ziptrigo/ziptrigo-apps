"""The feedback page (issue #82): an HTMX form view, following the convention in CLAUDE.md, and
the page it redirects to afterwards (post/redirect/get, so a refresh never sends it twice)."""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core import ratelimit
from apps.core.htmx import hx_redirect, is_htmx

from ..forms import FeedbackForm
from ..models import MAX_DESCRIPTION_LENGTH
from ..services import submit_feedback

# Set in the session by a successful submission and popped by the thank-you page, so that page
# can only be reached by actually submitting something.
SUBMITTED_SESSION_KEY = 'feedback_submitted'


@login_required
@require_http_methods(['GET', 'POST'])
def feedback_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Show the feedback form, and save the feedback with it.

    The `FEEDBACK_USER` rate limit is checked only once the form has validated (same reasoning as
    `apps.accounts.views.register_page`): a rejected submission doesn't spend any of the budget.
    Each submission emails every superuser, which is what the limit protects. On success,
    redirects to the thank-you page.
    """
    if request.method == 'POST':
        form = FeedbackForm(request.POST)
        status = 422
        if form.is_valid():
            limited = ratelimit.hit_user(request.user, 'FEEDBACK_USER')
            if not limited.allowed:
                return ratelimit.web_response(request, limited, retarget='#feedback-msg')

            submit_feedback(request.user, form.cleaned_data['description'])
            request.session[SUBMITTED_SESSION_KEY] = True
            return hx_redirect(request, reverse('feedback:thanks'))
    else:
        form = FeedbackForm()
        status = 200

    partial = request.method == 'POST' and is_htmx(request)
    context = {
        'form': form,
        'max_length': MAX_DESCRIPTION_LENGTH,
        'max_length_label': f'{MAX_DESCRIPTION_LENGTH:,}',
        # Only a bare partial response carries the out-of-band `#feedback-msg` reset; the full
        # page already has that element, so adding it there would duplicate the id.
        'reset_message': partial,
    }
    template = 'feedback/partials/feedback_form.html' if partial else 'feedback/feedback.html'
    return render(request, template, context, status=status)


@login_required
@require_http_methods(['GET'])
def thanks_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Confirm that the feedback was received (and, for a confirmed address, a copy emailed).

    Reachable only right after a submission: the session flag it sets is consumed here, so
    visiting directly (or refreshing) goes back to the form.
    """
    if not request.session.pop(SUBMITTED_SESSION_KEY, False):
        return redirect('feedback:submit')
    return render(request, 'feedback/thanks.html')
