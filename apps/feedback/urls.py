from django.urls import path

from .views import feedback_page, thanks_page

app_name = 'feedback'

urlpatterns = [
    path('', feedback_page, name='submit'),
    path('thanks/', thanks_page, name='thanks'),
]
