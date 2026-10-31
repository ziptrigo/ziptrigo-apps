from django.urls import path

from .views import credits_history_page, redeem, redeem_page

app_name = 'billing'

urlpatterns = [
    path('credits/', credits_history_page, name='credits-history'),
    path('redeem/', redeem, name='redeem'),
    path('redeem/<str:code>/', redeem_page, name='redeem-code'),
]
