from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render

from apps.accounts.http import AuthenticatedHttpRequest

from .models import CreditTransaction
from .services import get_balance


@login_required
def credits_history_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Render the credits usage history page for the authenticated user."""
    user = request.user

    queryset = CreditTransaction.objects.filter(user=user).order_by('-created_at', '-id')
    paginator = Paginator(queryset, per_page=25)

    page_number = request.GET.get('page', '1')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'credits_balance': get_balance(user),
    }
    return render(request, 'billing/credits_history.html', context)
