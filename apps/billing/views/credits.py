"""The Credits page: Usage and History tabs at `/billing/credits/` (issue #347).

`?tab=usage` (the default) is the usage report (`views.usage`), `?tab=history` the ledger with the
promo code form. An unknown tab is Usage. Every "Top up" link on the site opens History
(`apps.billing.services.credits_url`).
"""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render

from apps.accounts.http import AuthenticatedHttpRequest

from ..forms import parse_params
from ..models import CreditTransaction
from ..services import get_balance
from ..services.links import DEFAULT_TAB, HISTORY, USAGE
from .usage import usage_context

#: Ledger rows per page of the History tab.
PAGE_SIZE = 25


@login_required
def credits_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Render the Credits page for the authenticated user, on the tab `?tab=` names."""
    user = request.user
    tab = HISTORY if request.GET.get('tab') == HISTORY else DEFAULT_TAB
    context: dict = {'tab': tab, 'credits_balance': get_balance(user)}
    if tab == USAGE:
        context.update(usage_context(request, user, parse_params(request.GET)))
        return render(request, 'billing/usage.html', context)

    queryset = CreditTransaction.objects.filter(user=user).order_by('-created_at', '-id')
    paginator = Paginator(queryset, per_page=PAGE_SIZE)
    context['page_obj'] = paginator.get_page(request.GET.get('page', '1'))
    return render(request, 'billing/credits_history.html', context)
