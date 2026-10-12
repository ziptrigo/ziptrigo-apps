from django.urls import path

from . import views

app_name = 'receipts'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('list/', views.receipt_list, name='list'),
    path('export.csv', views.export_csv, name='export'),
    # Upload: the page, then two JSON endpoints the script calls around its direct S3 PUT.
    path('upload/', views.upload_page, name='upload'),
    path('upload/start/', views.upload_start, name='upload-start'),
    path('upload/<uuid:receipt_id>/complete/', views.upload_complete, name='upload-complete'),
    path('trash/', views.trash_page, name='trash'),
    # Settings: the address, accepted senders and currency, and the category editor.
    path('settings/', views.settings_page, name='settings'),
    path('settings/regenerate/', views.regenerate_address, name='regenerate'),
    path('settings/inbox/', views.update_inbox, name='settings-inbox'),
    path('settings/categories/', views.category_create, name='category-create'),
    path(
        'settings/categories/<int:category_id>/rename/',
        views.category_rename,
        name='category-rename',
    ),
    path('settings/categories/<int:category_id>/move/', views.category_move, name='category-move'),
    path(
        'settings/categories/<int:category_id>/delete/',
        views.category_delete,
        name='category-delete',
    ),
    # One receipt: the page (GET) and its form (POST), then the actions on it.
    path('<uuid:receipt_id>/', views.detail, name='detail'),
    path('<uuid:receipt_id>/body/', views.body_view, name='body'),
    path('<uuid:receipt_id>/download/', views.download, name='download'),
    path('<uuid:receipt_id>/reprocess/', views.reprocess_receipt, name='reprocess'),
    path('<uuid:receipt_id>/trash/', views.trash_receipt, name='trash-receipt'),
    path('<uuid:receipt_id>/restore/', views.restore_receipt, name='restore'),
    path('<uuid:receipt_id>/delete/', views.delete_receipt_forever, name='delete'),
    path(
        '<uuid:receipt_id>/dismiss-duplicate/',
        views.dismiss_duplicate_flag,
        name='dismiss-duplicate',
    ),
]
