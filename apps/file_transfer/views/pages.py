from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from apps.accounts.http import AuthenticatedHttpRequest


@login_required
def index(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Render the file transfer home page. The transfer list loads into it over HTMX."""
    return render(request, 'file_transfer/index.html')


@login_required
def transfer_list(request: AuthenticatedHttpRequest) -> HttpResponse:
    """HTMX partial: the user's transfers."""
    return render(request, 'file_transfer/partials/transfer_list.html', {'transfers': []})
