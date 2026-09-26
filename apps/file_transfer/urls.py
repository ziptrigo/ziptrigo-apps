from django.urls import path

from .views import index, transfer_list

app_name = 'file_transfer'

urlpatterns = [
    path('', index, name='index'),
    path('transfers/', transfer_list, name='transfer-list'),
]
