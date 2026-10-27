from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def home(request: HttpRequest) -> HttpResponse:
    """Landing page listing the product apps."""
    return render(request, 'core/home.html')
