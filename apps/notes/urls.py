from django.urls import path

from . import views

app_name = 'notes'

urlpatterns = [
    path('', views.notes_page, name='list'),
    path('archived/', views.archived_page, name='archived'),
    path('trash/', views.trash_page, name='trash'),
    path('list/', views.note_list, name='list-partial'),
    path('new/', views.note_new, name='new'),
    path('preview/', views.note_preview, name='preview'),
    path('reorder/', views.note_reorder, name='reorder'),
    path('search/', views.search_page, name='search'),
    path('search/list/', views.search_list, name='search-list'),
    path('filters/', views.filters_page, name='filters'),
    path('filters/counts/', views.filter_counts_view, name='filter-counts'),
    path('filters/sidebar/', views.sidebar_view, name='filter-sidebar'),
    path('filters/reorder/', views.filter_reorder, name='filter-reorder'),
    path('filters/<uuid:filter_id>/', views.filter_page, name='filter'),
    path('filters/<uuid:filter_id>/rename/', views.filter_rename, name='filter-rename'),
    path('filters/<uuid:filter_id>/update/', views.filter_update, name='filter-update'),
    path('filters/<uuid:filter_id>/delete/', views.filter_delete, name='filter-delete'),
    path('tags/', views.tags_page, name='tags'),
    path('tags/reorder/', views.tag_reorder, name='tags-reorder'),
    path('tags/<uuid:tag_id>/edit/', views.tag_edit, name='tag-edit'),
    path('tags/<uuid:tag_id>/delete/', views.tag_delete, name='tag-delete'),
    path('<uuid:note_id>/', views.note_page, name='note'),
    path('<uuid:note_id>/edit/', views.note_edit, name='edit'),
    path('<uuid:note_id>/checklist/', views.note_checklist, name='checklist'),
    path('<uuid:note_id>/tags/', views.note_tags, name='tags-set'),
    path('<uuid:note_id>/share/', views.share_panel, name='share'),
    path('<uuid:note_id>/share/new/', views.share_create, name='share-create'),
    path('<uuid:note_id>/share/<uuid:link_id>/edit/', views.share_edit, name='share-edit'),
    path('<uuid:note_id>/share/<uuid:link_id>/revoke/', views.share_revoke, name='share-revoke'),
    path('<uuid:note_id>/archive/', views.note_archive, name='archive'),
    path('<uuid:note_id>/unarchive/', views.note_unarchive, name='unarchive'),
    path('<uuid:note_id>/trash/', views.note_trash, name='trash-note'),
    path('<uuid:note_id>/restore/', views.note_restore, name='restore'),
    path('<uuid:note_id>/delete/', views.note_delete, name='delete'),
    path('<uuid:note_id>/duplicate/', views.note_duplicate, name='duplicate'),
    path('<uuid:note_id>/download/', views.note_download, name='download'),
    path('<uuid:note_id>/history/', views.history_list, name='history'),
    path(
        '<uuid:note_id>/history/<uuid:version_id>/', views.history_version, name='history-version'
    ),
    path(
        '<uuid:note_id>/history/<uuid:version_id>/restore/',
        views.history_restore,
        name='history-restore',
    ),
]
