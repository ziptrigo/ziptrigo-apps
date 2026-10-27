from django.urls import path

from . import views

app_name = 'core'

urlpatterns = [
    path('', views.home, name='home'),
    path('notifications/', views.notifications_page, name='notifications'),
    path('notifications/list/', views.notification_list, name='notification-list'),
    path('notifications/bell/', views.notification_bell, name='notification-bell'),
    path('notifications/dropdown/', views.notification_dropdown, name='notification-dropdown'),
    path('notifications/read-all/', views.notification_read_all, name='notification-read-all'),
    path(
        'notifications/<uuid:notification_id>/read/',
        views.notification_read,
        name='notification-read',
    ),
    path(
        'notifications/<uuid:notification_id>/dismiss/',
        views.notification_dismiss,
        name='notification-dismiss',
    ),
    path(
        'notifications/<uuid:notification_id>/open/',
        views.notification_open,
        name='notification-open',
    ),
]
