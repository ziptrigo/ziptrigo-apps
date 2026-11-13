from django.urls import path

from .views import costs_page, credits_history_page, redeem, redeem_page

app_name = 'billing'

urlpatterns = [
    path('credits/', credits_history_page, name='credits-history'),
    path('costs/', costs_page, name='costs'),
    path('redeem/', redeem, name='redeem'),
    path('redeem/<str:code>/', redeem_page, name='redeem-code'),
]
