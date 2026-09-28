"""The six sender/recipient emails (spec section 8), all through `apps.core.services.email`, from
and reply-to `no-reply@ziptrigo.com` (that's `AWS_SES_SENDER`'s default, and SES replies default
to the From address when no Reply-To is set, so nothing extra is needed for that).

Each function is a `django.tasks` task -- call `<name>.enqueue(...)` from a view, service or job,
never `<name>.call(...)` directly, so a slow send never blocks a request or a job run. Arguments
are plain strings/ids (never model instances): task backends serialize them, and the database
backend persists them between enqueue and execution.
"""

from django.conf import settings
from django.tasks import task
from django.urls import reverse

from apps.core.services.email import send_email

from ..models import FileTransferSettings, Transfer, TransferFile
from .naming import transfer_display_name


def dashboard_url() -> str:
    return f'{settings.BASE_URL}{reverse("file_transfer:dashboard")}'


@task
def send_transfer_notification(transfer_id: str, recipient_email: str) -> None:
    """ "Transfer sent" -- to each recipient, on send."""
    try:
        transfer = Transfer.objects.get(id=transfer_id)
    except Transfer.DoesNotExist:
        return

    name = transfer_display_name(transfer)
    body_lines = [f'You have received files: {name}.', '']
    if transfer.message:
        body_lines += [transfer.message, '']
    body_lines.append(f'Download: {transfer.absolute_download_url}')
    if transfer.expires_at:
        body_lines.append(f'This link expires on {transfer.expires_at:%Y-%m-%d %H:%M} UTC.')

    send_email(
        to=recipient_email,
        subject=f'{name} was sent to you via ZipTrigo',
        text_body='\n'.join(body_lines),
    )


@task
def send_sender_copy(transfer_id: str) -> None:
    """Copy/confirmation -- to the sender, on send."""
    try:
        transfer = Transfer.objects.select_related('owner').get(id=transfer_id)
    except Transfer.DoesNotExist:
        return
    if not transfer.owner or not transfer.owner.email:
        return

    name = transfer_display_name(transfer)
    recipients = ', '.join(r.email for r in transfer.recipients.all())
    body = (
        f'Your transfer "{name}" was sent to: {recipients}.\n\n'
        f'Download link: {transfer.absolute_download_url}\n'
        f'Manage this transfer: {dashboard_url()}'
    )
    send_email(
        to=transfer.owner.email,
        subject=f'Your transfer "{name}" is on its way',
        text_body=body,
    )


@task
def send_download_notification(transfer_id: str, file_id: str | None) -> None:
    """ "File downloaded" -- to the sender, on each download, if `notify_on_download`.

    `file_id=None` means the zip ("download all", spec section 5)."""
    try:
        transfer = Transfer.objects.select_related('owner').get(id=transfer_id)
    except Transfer.DoesNotExist:
        return
    if not transfer.notify_on_download or not transfer.owner or not transfer.owner.email:
        return

    if file_id is None:
        file_name = 'all files'
    else:
        try:
            file = TransferFile.objects.get(id=file_id)
            file_name = file.name
        except TransferFile.DoesNotExist:
            file_name = 'a file'

    name = transfer_display_name(transfer)
    send_email(
        to=transfer.owner.email,
        subject=f'"{file_name}" was downloaded',
        text_body=(
            f'Someone just downloaded "{file_name}" from your transfer "{name}".\n\n'
            f'Manage this transfer: {dashboard_url()}'
        ),
    )


@task
def send_expires_soon_notification(transfer_id: str) -> None:
    """ "Expires tomorrow" -- to the sender, one day before a time-based expiry."""
    try:
        transfer = Transfer.objects.select_related('owner').get(id=transfer_id)
    except Transfer.DoesNotExist:
        return
    if not transfer.owner or not transfer.owner.email:
        return

    name = transfer_display_name(transfer)
    send_email(
        to=transfer.owner.email,
        subject=f'Your transfer "{name}" expires tomorrow',
        text_body=(
            f'Your transfer "{name}" expires tomorrow'
            f'{f" ({transfer.expires_at:%Y-%m-%d %H:%M} UTC)" if transfer.expires_at else ""}.\n\n'
            f'Manage this transfer: {dashboard_url()}'
        ),
    )


@task
def send_suspended_notification(transfer_id: str) -> None:
    """ "Suspended, out of credits" -- to the sender, on suspension."""
    try:
        transfer = Transfer.objects.select_related('owner').get(id=transfer_id)
    except Transfer.DoesNotExist:
        return
    if not transfer.owner or not transfer.owner.email:
        return

    name = transfer_display_name(transfer)
    grace_days = FileTransferSettings.load().suspension_grace_days
    send_email(
        to=transfer.owner.email,
        subject=f'Your transfer "{name}" was suspended (out of credits)',
        text_body=(
            f'Your transfer "{name}" ran out of credits and has been suspended: the download '
            'link is no longer working.\n\n'
            f'Top up your credits within {grace_days} day(s) to automatically resume it, or its '
            'files will be deleted.\n\n'
            f'Manage this transfer: {dashboard_url()}'
        ),
    )


@task
def send_files_deleted_notification(transfer_id: str) -> None:
    """ "Files deleted" -- to the sender, after suspension's grace period or natural expiry."""
    try:
        transfer = Transfer.objects.select_related('owner').get(id=transfer_id)
    except Transfer.DoesNotExist:
        return
    if not transfer.owner or not transfer.owner.email:
        return

    name = transfer_display_name(transfer)
    send_email(
        to=transfer.owner.email,
        subject=f'Files for "{name}" have been deleted',
        text_body=f'The files for your transfer "{name}" have been deleted and are no longer available.',
    )
