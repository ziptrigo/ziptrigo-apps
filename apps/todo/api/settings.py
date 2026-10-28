"""The settings and inbox endpoints (`/api/todo/settings`, `/api/todo/inbox`): the default due time,
the default reminder, who may add tasks by email, what the account has used this month and what its
attachments cost, and the address tasks can be emailed to. Each calls the service the Settings page
uses; changes spend `TODO_EDIT_USER`, regenerating the address `TODO_REGENERATE_USER`.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import User

from .. import services
from ..models import TodoAccount, TodoSettings
from ..schemas import InboxSchema, SettingsSchema, SettingsUpdateSchema
from ..services import reminders
from .common import charge, mapped_errors
from .router import router


def _settings(user: User) -> dict:
    account = services.get_account(user)
    config = TodoSettings.load()
    used, free = services.month_usage(account, config)
    return {
        'default_due_time': account.default_due_time,
        'default_reminder_minutes': account.default_reminder_minutes,
        'default_reminder_channel': account.default_reminder_channel,
        'sender_allowlist': list(account.sender_allowlist),
        'tasks_created_this_month': used,
        'free_tasks_per_month': free,
        'task_price': config.task_price,
        'creation_blocked': account.is_blocked,
        'stored_bytes': account.stored_bytes,
        'storage_credits_per_day': f'{services.daily_storage_rate(account.stored_bytes):f}',
        'storage_frozen': account.is_frozen,
    }


def _save(account: TodoAccount, user: User, changes: dict) -> None:
    """Apply the changes, all or none (a refusal rolls the others back)."""
    with transaction.atomic():
        if 'default_due_time' in changes:
            if changes['default_due_time'] is None:
                raise ValidationError({'default_due_time': 'Enter a time.'})
            services.update_default_due_time(account, changes['default_due_time'])
        if 'default_reminder_minutes' in changes or 'default_reminder_channel' in changes:
            minutes = (
                changes['default_reminder_minutes']
                if 'default_reminder_minutes' in changes
                else account.default_reminder_minutes
            )
            channel = (
                changes['default_reminder_channel']
                if 'default_reminder_channel' in changes
                else account.default_reminder_channel
            )
            reminders.set_defaults(account, minutes=minutes, channel=channel or '')
        if 'sender_allowlist' in changes:
            services.update_inbox_settings(user, sender_allowlist=changes['sender_allowlist'] or [])


def _check(changes: dict) -> None:
    """The refusals of `_save` that need no database, told before the budget is spent."""
    if 'default_due_time' in changes and changes['default_due_time'] is None:
        raise ValidationError({'default_due_time': 'Enter a time.'})
    if changes.get('default_reminder_minutes') is not None:
        reminders.clean_minutes(changes['default_reminder_minutes'])
    if 'default_reminder_channel' in changes:
        reminders.clean_channel(changes['default_reminder_channel'] or '')


@router.get('/settings', response=SettingsSchema)
def get_settings(request):
    """The account's settings and, read-only, its usage this month and its storage."""
    return _settings(request.auth)


@router.patch('/settings', response=SettingsSchema)
def update_settings(request, payload: SettingsUpdateSchema):
    """Change the default due time, the default reminder (`default_reminder_minutes` and
    `default_reminder_channel`; `null` minutes: no default reminder) and/or who may add tasks by
    email (`sender_allowlist`; `[]` or `null`: anyone). Only the fields present are applied, all or
    none. They apply to tasks created (or first given a date) afterwards; changing the default due
    time moves the reminders of date-only tasks.

    A missing due time, a reminder time outside 0 to 43,200 minutes and an unknown channel are
    refused before `TODO_EDIT_USER` is spent; a sender allowlist the service then refuses still
    spends one."""
    changes = payload.model_dump(exclude_unset=True)
    if changes:
        with mapped_errors():
            _check(changes)
            charge(request, 'TODO_EDIT_USER')
            _save(services.get_account(request.auth), request.auth, changes)
    return _settings(request.auth)


def _inbox(user: User) -> dict:
    account = services.get_account(user)
    return {
        'address': services.inbox_address(services.active_inbox_code(account).code),
        'sender_allowlist': list(account.sender_allowlist),
    }


@router.get('/inbox', response=InboxSchema)
def get_inbox(request):
    """The address tasks can be emailed to (`todo+<code>@...`: the subject becomes the title, the
    body the description and the attachments the task's attachments) and who may send to it (an
    empty list accepts anyone; change it with `PATCH /settings`)."""
    return _inbox(request.auth)


@router.post('/inbox/regenerate', response=InboxSchema)
def regenerate_inbox(request):
    """Retire the current address immediately and issue a new one. Mail still sent to the old
    address is dropped."""
    charge(request, 'TODO_REGENERATE_USER')
    services.regenerate_inbox_code(request.auth)
    return _inbox(request.auth)
