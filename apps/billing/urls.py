from django.urls import path

from .views import credits_history_page

app_name = 'billing'

urlpatterns = [
    path('credits/', credits_history_page, name='credits-history'),
]
