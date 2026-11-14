from django.urls import path

from . import views

app_name = 'world_time'

urlpatterns = [
    path('', views.home_page, name='home'),
    path('tabs/new/', views.tab_create, name='tab-create'),
    path('search/', views.location_search, name='search'),
    path('<uuid:tab_id>/', views.tab_page, name='tab'),
    path('<uuid:tab_id>/rename/', views.tab_rename, name='tab-rename'),
    path('<uuid:tab_id>/delete/', views.tab_delete, name='tab-delete'),
    path('<uuid:tab_id>/locations/', views.location_add, name='location-add'),
    path('<uuid:tab_id>/locations/reorder/', views.location_reorder, name='location-reorder'),
    path(
        '<uuid:tab_id>/locations/<uuid:location_id>/label/',
        views.location_label,
        name='location-label',
    ),
    path(
        '<uuid:tab_id>/locations/<uuid:location_id>/home/',
        views.location_home,
        name='location-home',
    ),
    path(
        '<uuid:tab_id>/locations/<uuid:location_id>/remove/',
        views.location_remove,
        name='location-remove',
    ),
]
