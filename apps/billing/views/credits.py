"""The Credits page: Usage and History tabs at `/billing/credits/` (issue #347).

`?tab=usage` (the default) is the usage report (`views.usage`), `?tab=history` the ledger with the
promo code form. An unknown tab is Usage. The Usage tab spends `BILLING_USAGE_USER`. Every "Top up" link on the site opens History
(`apps.billing.services.credits_url`).
"""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core import ratelimit
from apps.core.ratelimit.responses import DEFAULT_MESSAGE

from ..forms import parse_params
from ..models import CreditTransaction
from ..services import get_balance
from ..services.links import DEFAULT_TAB, HISTORY, USAGE
from .usage import limited_context, usage_context

#: Ledger rows per page of the History tab.
PAGE_SIZE = 25


@login_required
def credits_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Render the Credits page for the authenticated user, on the tab `?tab=` names."""
    user = request.user
    tab = HISTORY if request.GET.get('tab') == HISTORY else DEFAULT_TAB
    context: dict = {'tab': tab, 'credits_balance': get_balance(user)}
    if tab == USAGE:
        params = parse_params(request.GET)
        # The report runs a grouped query over the ledger: the page spends the same budget as the
        # htmx refresh and the API, or a script could loop on the page instead. Over it, the
        # filters and the message come back and no query runs. The History tab is not charged.
        result = ratelimit.hit_user(user, 'BILLING_USAGE_USER')
        if not result.allowed:
            context.update(limited_context(params, DEFAULT_MESSAGE))
            response = render(request, 'billing/usage.html', context, status=429)
            response['Retry-After'] = str(result.retry_after)
            return response
        context.update(usage_context(request, user, params))
        return render(request, 'billing/usage.html', context)

    queryset = CreditTransaction.objects.filter(user=user).order_by('-created_at', '-id')
    paginator = Paginator(queryset, per_page=PAGE_SIZE)
    context['page_obj'] = paginator.get_page(request.GET.get('page', '1'))
    return render(request, 'billing/credits_history.html', context)
