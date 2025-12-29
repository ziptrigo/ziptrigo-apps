from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from .models import CreditTransaction


@login_required
def account_page(request: HttpRequest) -> HttpResponse:
    """Render the account settings page for the authenticated user."""
    return render(request, 'account.html')


@login_required
def credits_history_page(request: HttpRequest) -> HttpResponse:
    """Render the credits usage history page for the authenticated user."""
    user = request.user

    queryset = CreditTransaction.objects.filter(user=user).order_by('-created_at', '-id')
    paginator = Paginator(queryset, per_page=25)

    page_number = request.GET.get('page', '1')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'credits_balance': user.credits,
    }
    return render(request, 'credits_history.html', context)
