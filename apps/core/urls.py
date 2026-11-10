from django.urls import path

from . import views

app_name = 'core'

urlpatterns = [
    path('', views.home, name='home'),
    # The legal documents (issue #96): public, and never behind the terms acceptance gate.
    path('privacy/', views.privacy_page, name='privacy'),
    path('terms/', views.terms_page, name='terms'),
    # Web Push (issue #239). The service worker and the manifest live at the root: a service
    # worker only controls the pages under its own path.
    path('sw.js', views.service_worker, name='service-worker'),
    path('manifest.webmanifest', views.manifest, name='manifest'),
    path('push/key/', views.push_public_key, name='push-key'),
    path('push/subscribe/', views.push_subscribe, name='push-subscribe'),
    path('push/unsubscribe/', views.push_unsubscribe, name='push-unsubscribe'),
    path('push/action/', views.push_action, name='push-action'),
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
