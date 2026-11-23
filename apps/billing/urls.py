from django.urls import path

from .views import costs_page, credits_page, credits_usage_partial, redeem, redeem_page

app_name = 'billing'

urlpatterns = [
    path('credits/', credits_page, name='credits-history'),
    path('credits/usage/', credits_usage_partial, name='credits-usage'),
    path('costs/', costs_page, name='costs'),
    path('redeem/', redeem, name='redeem'),
    path('redeem/<str:code>/', redeem_page, name='redeem-code'),
]
