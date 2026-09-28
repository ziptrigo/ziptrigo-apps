from django.urls import path

from . import views
from .views import anonymous as anon_views

app_name = 'file_transfer'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('transfers/', views.transfer_list, name='transfer-list'),
    # Send flow: a draft transfer, its files (added/removed/uploaded via JSON -- see
    # views/uploads.py), then the options form submit.
    path('send/', views.send_page, name='send'),
    path('send/<uuid:draft_id>/files/', views.add_file, name='send-add-file'),
    path(
        'send/<uuid:draft_id>/files/<uuid:file_id>/parts/',
        views.part_urls,
        name='send-part-urls',
    ),
    path(
        'send/<uuid:draft_id>/files/<uuid:file_id>/complete/',
        views.complete_file,
        name='send-complete-file',
    ),
    path(
        'send/<uuid:draft_id>/files/<uuid:file_id>/remove/',
        views.remove_file,
        name='send-remove-file',
    ),
    path('send/<uuid:draft_id>/submit/', views.send_submit, name='send-submit'),
    path('sent/<uuid:transfer_id>/', views.sent_page, name='sent'),
    # Dashboard actions.
    path('<uuid:transfer_id>/disable/', views.disable, name='disable'),
    path('<uuid:transfer_id>/reenable/', views.reenable, name='reenable'),
    path('<uuid:transfer_id>/delete/', views.delete_now, name='delete'),
    path('<uuid:transfer_id>/settings/', views.update_settings, name='update-settings'),
    path('<uuid:transfer_id>/recipients/', views.add_recipients, name='add-recipients'),
    path(
        '<uuid:transfer_id>/recipients/<uuid:recipient_id>/resend/',
        views.resend_recipient,
        name='resend-recipient',
    ),
    # Anonymous send flow (spec section 2 phase 2): session-owned, not login-owned -- see
    # views/anonymous.py. One page (anon-send) renders whichever step the session's current
    # anonymous transfer is in.
    path('send/anon/', anon_views.send_page, name='anon-send'),
    path('send/anon/<uuid:draft_id>/files/', anon_views.add_file, name='anon-send-add-file'),
    path(
        'send/anon/<uuid:draft_id>/files/<uuid:file_id>/parts/',
        anon_views.part_urls,
        name='anon-send-part-urls',
    ),
    path(
        'send/anon/<uuid:draft_id>/files/<uuid:file_id>/complete/',
        anon_views.complete_file,
        name='anon-send-complete-file',
    ),
    path(
        'send/anon/<uuid:draft_id>/files/<uuid:file_id>/remove/',
        anon_views.remove_file,
        name='anon-send-remove-file',
    ),
    path(
        'send/anon/<uuid:draft_id>/confirm/',
        anon_views.start_confirmation,
        name='anon-send-confirm',
    ),
    path(
        'send/anon/<uuid:draft_id>/confirm/code/',
        anon_views.confirm_code,
        name='anon-send-confirm-code',
    ),
    path(
        'send/anon/<uuid:draft_id>/confirm/resend/',
        anon_views.resend_confirmation,
        name='anon-send-confirm-resend',
    ),
    path(
        'send/anon/<uuid:draft_id>/confirm/link/<str:token>/',
        anon_views.confirm_link,
        name='anon-send-confirm-link',
    ),
    path('sent/anon/<uuid:transfer_id>/', anon_views.sent_page, name='anon-sent'),
]
