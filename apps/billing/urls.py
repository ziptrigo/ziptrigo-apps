from django.urls import path

from .views import credits_history_page

urlpatterns = [
    path('credits/', credits_history_page, name='credits-history-page'),
]
