from django.urls import path

from . import views

app_name = 'todo'

urlpatterns = [
    path('', views.today_page, name='today'),
    path('upcoming/', views.upcoming_page, name='upcoming'),
    path('all/', views.all_page, name='all'),
    path('completed/', views.completed_page, name='completed'),
    path('trash/', views.trash_page, name='trash'),
    path('list/', views.task_list, name='list'),
    path('new/', views.task_create, name='create'),
    path('reorder/', views.reorder_view, name='reorder'),
    path('reschedule-overdue/', views.reschedule_overdue_view, name='reschedule-overdue'),
    path('labels/', views.labels_page, name='labels'),
    path('labels/reorder/', views.label_reorder, name='label-reorder'),
    path('labels/<uuid:label_id>/edit/', views.label_edit, name='label-edit'),
    path('labels/<uuid:label_id>/delete/', views.label_delete, name='label-delete'),
    path('settings/', views.settings_page, name='settings'),
    path(
        'attachments/<uuid:attachment_id>/download/',
        views.attachment_download,
        name='attachment-download',
    ),
    path(
        'attachments/<uuid:attachment_id>/delete/',
        views.attachment_delete,
        name='attachment-delete',
    ),
    path('<int:number>/', views.task_page, name='task'),
    path('<int:number>/edit/', views.task_edit, name='edit'),
    path('<int:number>/complete/', views.task_complete, name='complete'),
    path('<int:number>/uncomplete/', views.task_uncomplete, name='uncomplete'),
    path('<int:number>/postpone/', views.task_postpone, name='postpone'),
    path('<int:number>/trash/', views.task_trash, name='trash-task'),
    path('<int:number>/restore/', views.task_restore, name='restore'),
    path('<int:number>/delete/', views.task_delete, name='delete'),
    path('<int:number>/recurrence/stop/', views.recurrence_stop, name='recurrence-stop'),
    path('<int:number>/checklist/', views.task_checklist, name='checklist'),
    path('<int:number>/subtasks/', views.subtask_add, name='subtask-add'),
    path('<int:number>/comments/', views.comment_add, name='comment-add'),
    path('<int:number>/comments/<uuid:comment_id>/edit/', views.comment_edit, name='comment-edit'),
    path(
        '<int:number>/comments/<uuid:comment_id>/delete/',
        views.comment_delete,
        name='comment-delete',
    ),
    path(
        '<int:number>/comments/<uuid:comment_id>/restore/',
        views.comment_restore,
        name='comment-restore',
    ),
    path(
        '<int:number>/comments/<uuid:comment_id>/history/',
        views.comment_history_view,
        name='comment-history',
    ),
    path(
        '<int:number>/comments/<uuid:comment_id>/deleted/',
        views.comment_deleted_view,
        name='comment-deleted',
    ),
    path('<int:number>/attachments/', views.attachment_list, name='attachment-list'),
    path('<int:number>/attachments/start/', views.attachment_start, name='attachment-start'),
    path(
        '<int:number>/attachments/<uuid:attachment_id>/complete/',
        views.attachment_complete,
        name='attachment-complete',
    ),
    path(
        '<int:number>/attachments/<uuid:attachment_id>/cancel/',
        views.attachment_cancel,
        name='attachment-cancel',
    ),
]
