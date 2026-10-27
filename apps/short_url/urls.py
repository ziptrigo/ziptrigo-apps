from django.urls import path

from .views import (
    dashboard,
    link_create,
    link_delete,
    link_detail,
    link_disable,
    link_edit,
    link_enable,
    link_list,
    link_qr,
)

app_name = 'short_url'

urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('list/', link_list, name='list'),
    path('new/', link_create, name='create'),
    path('<uuid:link_id>/', link_detail, name='detail'),
    path('<uuid:link_id>/edit/', link_edit, name='edit'),
    path('<uuid:link_id>/qr.png', link_qr, name='qr'),
    path('<uuid:link_id>/enable/', link_enable, name='enable'),
    path('<uuid:link_id>/disable/', link_disable, name='disable'),
    path('<uuid:link_id>/delete/', link_delete, name='delete'),
]
