"""What To-do costs, for the Costs page (`apps.core.costs`)."""

from datetime import date

from apps.accounts.models import User
from apps.accounts.services.timezones import user_today
from apps.billing.services import get_storage_price_per_gb_per_day
from apps.core.costs import CostSection, charge, free, plural, storage, storage_usage
from apps.core.datetime_format import format_date

from ..models import Task, TodoAccount, TodoSettings
from .billing import month_key


def _first_of_next_month(today: date) -> date:
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)


def cost_section(user: User) -> CostSection:
    config = TodoSettings.load()
    allowance = config.free_tasks_per_month
    lines = []
    if allowance:
        lines.append(
            free(
                'Add a task',
                f'The first {allowance:,} each month. Subtasks, emailed tasks and each repeat of a '
                'repeating task count.',
            )
        )
        lines.append(
            charge(
                f'Each task beyond {allowance:,} in a month',
                config.task_price,
                'per task',
                'Charged when the task is added.',
            )
        )
    else:
        lines.append(
            charge('Add a task', config.task_price, 'per task', 'Charged when the task is added.')
        )
    lines += [
        storage('Attach files', 'Files on tasks and on comments.', unit='per day'),
        free('Reading dates from a title, reminders, comments, search and saved filters'),
    ]

    # Read only: opening the Costs page must not create a To-do account.
    account = (
        TodoAccount.objects.filter(user=user)
        .only('month_key', 'month_task_count', 'stored_bytes', 'frozen_at')
        .first()
    )
    usage = []
    if allowance and config.task_price > 0:
        used = 0
        if account is not None and account.month_key == month_key(user):
            used = account.month_task_count
        renews = format_date(_first_of_next_month(user_today(user)), 'full')
        usage.append(
            f'This month: {min(used, allowance):,} of {allowance:,} free tasks used. '
            f'The allowance starts again on {renews}.'
        )
    if account is not None and account.stored_bytes:
        usage.append(storage_usage(account.stored_bytes, get_storage_price_per_gb_per_day()))
    if account is not None and account.frozen_at is not None:
        usage.append('File storage is paused until you top up.')
    unpaid = Task.objects.filter(user=user, unpaid_at__isnull=False).count()
    if unpaid:
        verb = 'is' if unpaid == 1 else 'are'
        usage.append(f'{plural(unpaid, "repeating task")} {verb} waiting to be paid for.')

    return CostSection(
        lines=lines,
        usage=usage,
        footnote="Out of credits: a repeating task's next one is still created and is paid once "
        'you top up; new tasks wait until then. File storage pauses; nothing is deleted.',
    )
