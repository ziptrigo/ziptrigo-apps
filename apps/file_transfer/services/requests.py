"""File requests (issue #306): a link the owner sends so that *other people* upload files to them.

A request is a `Transfer` with `kind='request'` (see `models.TransferKind`), so storage, metering,
the download page and its zip, expiry, suspension, takedown, auto-hold and the block list all
apply unchanged. What is different, and lives here:

- **Creating one** (`create_request`, shared by the web form and `POST /api/ft/requests`): it is
  `ACTIVE` from the start (nothing is uploaded by the owner, so there is no draft step), free to
  create, and needs the same 1-credit minimum balance as a send. Storage is billed to the owner
  per day like a transfer's, on `size_bytes`, which is kept equal to the sum of the request's
  *uploaded* files.
- **Open vs Done** is `Transfer.done_at`, not a status. `mark_done` closes it to uploads (and
  switches the link to the download view); `reopen` is the owner undoing that. Uploading itself is
  `services.request_upload`; who may use the link is `services.request_access`.
- **Changing one** (`update_request`): title, message, audience and invitees, the limits, expiry
  and password, all or nothing.

Every owner action here goes through the same refusals a transfer's dashboard action has: the
request must be actionable (not ended), and a request on hold for abuse review is frozen except
for disabling it (`TransferHeldError`).
"""

import logging
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.template.defaultfilters import filesizeformat
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.services import InsufficientCreditsError, get_balance
from apps.core import ratelimit
from apps.core.services import notifications

from ..models import (
    FileTransferSettings,
    RequestInvite,
    Transfer,
    TransferFile,
    TransferKind,
    TransferStatus,
    UploadAudience,
    ZipStatus,
)
from . import limits
from .actions import ChargeEmails, _require_actionable, _require_not_held, charge_recipient_emails
from .blocklist import check_not_blocked
from .emails import send_request_done_email
from .expiry_choices import resolve_expiry
from .password import hash_password
from .request_invites import normalize_invite_emails, queue_invite_emails
from .send import MIN_BALANCE_TO_SEND
from .storage import S3Storage, get_storage

logger = logging.getLogger(__name__)

#: A request's title: 1 to this many characters.
TITLE_MAX_LENGTH = 100

NOT_ACCEPTING_MESSAGE = 'This link is not accepting files right now.'

_GB = 1024**3


# -- Validation helpers --------------------------------------------------------------------------


def clean_title(raw: object) -> str:
    """A request's title: control characters removed, stripped, 1 to 100 characters."""
    text = ''.join(ch for ch in str(raw or '') if unicodedata.category(ch) != 'Cc').strip()
    if not text:
        raise ValidationError({'title': 'Give the request a title.'})
    if len(text) > TITLE_MAX_LENGTH:
        raise ValidationError(
            {'title': f'Keep the title to {TITLE_MAX_LENGTH} characters or fewer.'}
        )
    return text


def clean_message(message: str) -> str:
    try:
        limits.validate_message(message)
    except ValidationError as exc:
        raise ValidationError({'message': exc.messages}) from exc
    return message


def clean_audience(value: str) -> str:
    if value not in UploadAudience.values:
        raise ValidationError({'upload_audience': 'Choose who can upload.'})
    return value


def clean_max_files(value: int | None, settings_row: FileTransferSettings) -> int | None:
    """`None` means the site's maximum; otherwise 1 to `logged_in_max_files`."""
    if value is None:
        return None
    site = settings_row.logged_in_max_files
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > site:
        raise ValidationError({'max_files': f'Max files must be between 1 and {site}.'})
    return value


def clean_max_total_size(value: int | None, settings_row: FileTransferSettings) -> int | None:
    """`None` means the site's maximum; otherwise 1 byte up to `logged_in_max_total_size_bytes`."""
    if value is None:
        return None
    site = settings_row.logged_in_max_total_size_bytes
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError({'max_total_size_bytes': 'Max total size must be above 0.'})
    if value > site:
        raise ValidationError(
            {'max_total_size_bytes': f'Max total size must be at most {limits.size_text(site)}.'}
        )
    return value


def gb_to_bytes(gb: Decimal | float | int | None) -> int | None:
    """The form's "Max total size" in GB, as whole bytes (`None` stays `None`)."""
    if gb is None:
        return None
    return int(Decimal(str(gb)) * _GB)


def _clean_invitees(emails: list[str], settings_row: FileTransferSettings) -> list[str]:
    cleaned = normalize_invite_emails(limits.validate_invitees(emails, settings_row))
    if not cleaned:
        raise ValidationError({'emails': 'Add at least one email address.'})
    return cleaned


# -- Creating ------------------------------------------------------------------------------------


@dataclass(slots=True)
class RequestOptions:
    """What the owner chooses when creating a request (the web form and the API's body)."""

    title: str
    message: str = ''
    upload_audience: str = UploadAudience.ANYONE
    emails: list[str] = field(default_factory=list)
    max_files: int | None = None
    max_total_size_bytes: int | None = None
    expiry_choice: str = 'none'
    expiry_date: datetime | None = None
    password: str = ''


@dataclass(slots=True)
class _Validated:
    title: str
    message: str
    audience: str
    invitees: list[str]
    max_files: int | None
    max_total_size_bytes: int | None
    expires_at: datetime | None


def validate_request_options(
    options: RequestOptions, settings_row: FileTransferSettings | None = None
) -> list[str]:
    """Validate everything about `options` except the owner's balance and block status. Returns
    the deduplicated invitee list (empty unless the audience is "Only these people"). A field
    problem is a `ValidationError` keyed by the field name."""
    return _validate(options, settings_row or FileTransferSettings.load()).invitees


def _validate(options: RequestOptions, settings_row: FileTransferSettings) -> _Validated:
    title = clean_title(options.title)
    message = clean_message(options.message)
    audience = clean_audience(options.upload_audience)
    invitees = (
        _clean_invitees(options.emails, settings_row) if audience == UploadAudience.SELECTED else []
    )
    max_files = clean_max_files(options.max_files, settings_row)
    max_total = clean_max_total_size(options.max_total_size_bytes, settings_row)
    expires_at = resolve_expiry(options.expiry_choice, options.expiry_date)
    return _Validated(title, message, audience, invitees, max_files, max_total, expires_at)


def charge_request_creation(user, email_count: int) -> None:
    """Spend the budgets a new request costs, once every other check has passed: one hit of
    `FT_REQUEST_CREATE_USER`, then one hit of `FT_RECIPIENT_EMAIL_USER` per invite email about to
    go out. Pass it to `create_request` as `charge`. Raises `ratelimit.RateLimitExceeded`."""
    result = ratelimit.hit_user(user, 'FT_REQUEST_CREATE_USER')
    if not result.allowed:
        raise ratelimit.RateLimitExceeded(
            retry_after=result.retry_after, message=ratelimit.retry_message(result.retry_after)
        )
    if email_count:
        charge_recipient_emails(user, email_count)


def create_request(
    owner: User,
    options: RequestOptions,
    *,
    ip: str | None = None,
    charge: ChargeEmails | None = None,
) -> Transfer:
    """Create a file request: `ACTIVE`, open, with its invitees (the audience "Only these people")
    emailed on commit.

    `charge` (the web view and the API pass `charge_request_creation` bound to the owner) is
    called with the number of invite emails once every check has passed and before anything is
    created: a refused request costs no budget, and one over the budget creates nothing.

    Raises:
        ValidationError: an option is invalid (keyed by field).
        blocklist.BlockedSenderError: the owner's email or `ip` is on the block list.
        InsufficientCreditsError: the owner's balance is below the 1-credit minimum.
        ratelimit.RateLimitExceeded: from `charge`.
    """
    settings_row = FileTransferSettings.load()
    valid = _validate(options, settings_row)
    check_not_blocked(email=owner.email, ip=ip)
    if get_balance(owner) < MIN_BALANCE_TO_SEND:
        raise InsufficientCreditsError(
            f'You need at least {MIN_BALANCE_TO_SEND} credit to request files.'
        )
    if charge is not None:
        charge(len(valid.invitees))

    now = timezone.now()
    with transaction.atomic():
        transfer = Transfer.objects.create(
            owner=owner,
            kind=TransferKind.REQUEST,
            status=TransferStatus.ACTIVE,
            title=valid.title,
            message=valid.message,
            upload_audience=valid.audience,
            max_files=valid.max_files,
            max_total_size_bytes=valid.max_total_size_bytes,
            expires_at=valid.expires_at,
            password_hash=hash_password(options.password) if options.password else '',
            # No downloads are counted against a request and nobody is emailed about them.
            max_downloads=None,
            notify_on_download=False,
            completed_at=now,
            last_billed_at=now,
        )
        for email in valid.invitees:
            RequestInvite.objects.create(transfer=transfer, email=email, last_sent_at=now)
        if valid.invitees:
            queue_invite_emails(transfer.id, valid.invitees)
    return transfer


# -- Changing ------------------------------------------------------------------------------------


def _require_request(transfer: Transfer) -> None:
    if not transfer.is_request:
        raise ValidationError('This is not a file request.')


def update_request(
    transfer: Transfer,
    data: dict,
    *,
    ip: str | None = None,
    charge: ChargeEmails | None = None,
) -> Transfer:
    """Change a request: only the keys present in `data` are applied, all or nothing (one
    transaction, the request row locked).

    Keys: `title`, `message`, `upload_audience`, `emails` (the whole invitee list: addresses not
    yet on it are invited -- emailed -- and the ones missing from it lose access), `max_files`
    and `max_total_size_bytes` (`None` is the site's maximum; never below what is already
    uploaded), `expiry_choice` (+ `expiry_date`), `password` and `remove_password`. Switching to
    "Only these people" needs at least one address. `emails` is ignored while the audience is not
    "Only these people" (the invitees are kept for when it is).

    `charge` is called with the number of new invitees after every check has passed and before
    anything is written, as `create_request` does.
    """
    _require_request(transfer)
    _require_actionable(transfer)
    _require_not_held(transfer)
    settings_row = FileTransferSettings.load()

    with transaction.atomic():
        locked = Transfer.objects.select_for_update().get(pk=transfer.pk)
        _require_actionable(locked)
        _require_not_held(locked)

        changes: dict = {}
        if 'title' in data:
            changes['title'] = clean_title(data['title'])
        if 'message' in data:
            changes['message'] = clean_message(data['message'])
        audience = locked.upload_audience
        if 'upload_audience' in data:
            audience = clean_audience(data['upload_audience'])
            changes['upload_audience'] = audience

        existing = list(locked.invites.all())
        to_add: list[str] = []
        to_remove: list[RequestInvite] = []
        if audience == UploadAudience.SELECTED and ('emails' in data or not existing):
            if 'emails' in data:
                wanted = _clean_invitees(data['emails'], settings_row)
            else:
                raise ValidationError({'emails': 'Add at least one email address.'})
            have = {invite.email.lower() for invite in existing}
            keep = {email.lower() for email in wanted}
            to_add = [email for email in wanted if email.lower() not in have]
            to_remove = [invite for invite in existing if invite.email.lower() not in keep]

        if 'max_files' in data:
            max_files = clean_max_files(data['max_files'], settings_row)
            have_files = locked.files.count()
            if max_files is not None and max_files < have_files:
                raise ValidationError(
                    {
                        'max_files': f'Already {have_files} '
                        f'{"file" if have_files == 1 else "files"} uploaded; '
                        f'choose at least {have_files}.'
                    }
                )
            changes['max_files'] = max_files
        if 'max_total_size_bytes' in data:
            max_total = clean_max_total_size(data['max_total_size_bytes'], settings_row)
            have_bytes = locked.files.aggregate(total=Sum('size'))['total'] or 0
            if max_total is not None and max_total < have_bytes:
                text = limits.size_text(have_bytes)
                raise ValidationError(
                    {'max_total_size_bytes': f'Already {text} uploaded; choose at least {text}.'}
                )
            changes['max_total_size_bytes'] = max_total
        if 'expiry_choice' in data:
            changes['expires_at'] = resolve_expiry(data['expiry_choice'], data.get('expiry_date'))
        if data.get('remove_password'):
            changes['password_hash'] = ''
        elif data.get('password'):
            changes['password_hash'] = hash_password(data['password'])

        if to_add:
            check_not_blocked(email=locked.owner.email if locked.owner else None, ip=ip)
            if charge is not None:
                charge(len(to_add))

        if changes:
            Transfer.objects.filter(pk=locked.pk).update(**changes)
        now = timezone.now()
        for invite in to_remove:
            invite.delete()
        for email in to_add:
            RequestInvite.objects.create(transfer=locked, email=email, last_sent_at=now)
        if to_add:
            queue_invite_emails(locked.id, to_add)

    transfer.refresh_from_db()
    return transfer


# -- Done and reopen -----------------------------------------------------------------------------


def done_notice_key(transfer_id: object) -> str:
    """The `Notification.key` of the owner's "request is done" notice."""
    return f'transfer_request_done:{transfer_id}'


def uploads_notice_key(transfer_id: object) -> str:
    """The `Notification.key` of the owner's debounced "new files" notice."""
    return f'transfer_request_uploads:{transfer_id}'


def requests_dashboard_url() -> str:
    """Where the owner's notices point: the dashboard's Requests tab (a relative path)."""
    return f'{reverse("file_transfer:dashboard")}?kind=request'


def owner_can_be_told(owner: User | None) -> bool:
    """Whether the owner's account can sign in, so a notice or email is worth sending."""
    return bool(owner and owner.is_active and owner.status == User.STATUS_ACTIVE)


def _notice_text_size(num_bytes: int) -> str:
    return str(filesizeformat(num_bytes)).replace('\xa0', ' ')


def _who_marked_done(transfer: Transfer) -> str:
    """Who marked it done, as the owner reads it: the account's name (else email), the anonymous
    uploader's typed name, else "someone"."""
    user = transfer.done_by_user
    if user is not None:
        return user.name or user.email
    return transfer.done_by_name or 'someone'


def mark_done(
    transfer: Transfer,
    *,
    by_user: User | None = None,
    by_name: str = '',
    storage: S3Storage | None = None,
) -> bool:
    """Mark an open request done: no more uploads, the link switches to the download view.
    Returns whether this call did it (`False`: it was already done -- losing the race is a no-op).

    Anyone who passes the access check may do it (`by_user` is a signed-in account, `by_name` an
    anonymous uploader's typed name); an uploader may only do so once the request has at least
    one uploaded file ("Upload at least one file first."), while the owner may do it with none.

    One conditional `UPDATE ... WHERE done_at IS NULL AND status = ACTIVE`. In the same
    transaction every file that is not `uploaded` yet (all uploaders') is deleted, the pending
    "new files" notice is cleared, and -- **only when someone other than the owner** did it -- the
    owner's in-app notice and email are queued; the unfinished multipart uploads are aborted in
    S3 after the commit, best effort.
    """
    _require_request(transfer)
    is_owner = by_user is not None and by_user.pk == transfer.owner_id
    storage = storage or get_storage()
    now = timezone.now()

    with transaction.atomic():
        locked = Transfer.objects.select_for_update().get(pk=transfer.pk)
        if locked.done_at is not None:
            transfer.refresh_from_db()
            return False
        if locked.status != TransferStatus.ACTIVE or locked.held_for_review_at:
            raise ValidationError(NOT_ACCEPTING_MESSAGE)
        if not is_owner and not locked.files.filter(uploaded=True).exists():
            raise ValidationError('Upload at least one file first.')

        done = Transfer.objects.filter(
            pk=locked.pk, done_at__isnull=True, status=TransferStatus.ACTIVE
        ).update(
            done_at=now,
            done_by_user=by_user,
            done_by_name='' if by_user is not None else by_name[:100],
            upload_notice_due_at=None,
            upload_notice_since=None,
        )
        if not done:
            transfer.refresh_from_db()
            return False

        pending = list(locked.files.filter(uploaded=False))
        aborts = [(f.storage_key, f.upload_id) for f in pending if f.upload_id]
        if pending:
            TransferFile.objects.filter(pk__in=[f.pk for f in pending]).delete()
        refresh_size(locked)
        invalidate_zip(locked, storage=storage)

        transfer.refresh_from_db()
        if not is_owner and owner_can_be_told(locked.owner):
            _notify_done(transfer)
            transfer_id = str(transfer.id)
            transaction.on_commit(lambda: send_request_done_email.enqueue(transfer_id))
        transaction.on_commit(lambda: _abort_uploads(aborts, storage))
    return True


def _abort_uploads(uploads: list[tuple[str, str]], storage: S3Storage) -> None:
    for key, upload_id in uploads:
        try:
            storage.abort_multipart_upload(key, upload_id)
        except Exception:
            logger.exception('Could not abort the unfinished upload of %s', key)


def _notify_done(transfer: Transfer) -> None:
    """The owner's in-app "request is done" notice, which also folds in (deletes) the pending "new
    files" one. Its own savepoint, logged and never raised: a notice must not undo the Done."""
    if transfer.owner_id is None:
        return
    try:
        with transaction.atomic():
            totals = transfer.files.filter(uploaded=True).aggregate(total=Sum('size'))
            count = transfer.files.filter(uploaded=True).count()
            size = totals['total'] or 0
            noun = 'file' if count == 1 else 'files'
            notifications.delete_for_key(
                transfer.owner_id, uploads_notice_key(transfer.pk), source='file_transfer'
            )
            notifications.delete_for_key(
                transfer.owner_id, done_notice_key(transfer.pk), source='file_transfer'
            )
            notifications.notify(
                transfer.owner_id,
                source='file_transfer',
                title=_cut(f'"{transfer.title}" is done', 140),
                body=f'{count} {noun}, {_notice_text_size(size)}. '
                f'Marked done by {_who_marked_done(transfer)}.',
                url=requests_dashboard_url(),
                key=done_notice_key(transfer.pk),
            )
    except Exception:
        logger.exception('Could not notify the owner that request %s is done', transfer.pk)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + '…'


def reopen(transfer: Transfer) -> Transfer:
    """Reopen a done request (owner only: the web dashboard and the API): the link is back in
    upload mode and the files stay. Nothing is sent to anyone. Uploaders can add and remove their
    own files again (their per-browser token still identifies their files in the same browser)."""
    _require_request(transfer)
    _require_actionable(transfer)
    _require_not_held(transfer)
    updated = Transfer.objects.filter(pk=transfer.pk, done_at__isnull=False).update(
        done_at=None, done_by_user=None, done_by_name=''
    )
    if not updated:
        transfer.refresh_from_db()
        if transfer.done_at is None:
            raise ValidationError('This request is already open.')
        raise ValidationError('This request could not be reopened. Try again.')
    transfer.refresh_from_db()
    return transfer


def owner_mark_done(transfer: Transfer, owner: User) -> bool:
    """The owner's "Mark as done" (dashboard, API): allowed with no files, never notifies."""
    _require_request(transfer)
    _require_actionable(transfer)
    _require_not_held(transfer)
    if transfer.status != TransferStatus.ACTIVE:
        raise ValidationError('Only an active request can be marked as done.')
    return mark_done(transfer, by_user=owner)


# -- Files ---------------------------------------------------------------------------------------


def refresh_size(transfer: Transfer) -> int:
    """Keep `size_bytes` equal to the sum of the request's *uploaded* files (metering bills it)."""
    size = transfer.files.filter(uploaded=True).aggregate(total=Sum('size'))['total'] or 0
    Transfer.objects.filter(pk=transfer.pk).update(size_bytes=size)
    transfer.size_bytes = size
    return size


def invalidate_zip(transfer: Transfer, *, storage: S3Storage | None = None) -> None:
    """Forget the "download all" zip: the request's files changed, so the next request for it
    builds a new one (and the old object is deleted once the surrounding transaction commits). A
    zip still being built is left alone."""
    stale = Transfer.objects.filter(
        pk=transfer.pk, zip_status__in=[ZipStatus.READY, ZipStatus.FAILED]
    )
    key = stale.filter(zip_status=ZipStatus.READY).values_list('zip_key', flat=True).first()
    if not stale.update(zip_status=ZipStatus.NONE, zip_key=''):
        return
    if transfer.zip_status != ZipStatus.BUILDING:
        transfer.zip_status = ZipStatus.NONE
        transfer.zip_key = ''
    if key:

        def _delete() -> None:
            try:
                (storage or get_storage()).delete_object(key)
            except Exception:
                logger.exception('Could not delete the stale zip %s', key)

        transaction.on_commit(_delete)


def abandoned_file_age_hours() -> int:
    """How old a request's unfinished upload must be before `cleanup_abandoned_files` deletes it."""
    return 24


def cleanup_abandoned_files(*, storage: S3Storage | None = None) -> int:
    """Delete the unfinished uploads (`uploaded=False`) of requests that are older than a day,
    aborting their multipart uploads. They count toward a request's limits (every uploader's
    files, finished or not), so without this a browser closed mid-upload would use up the
    request's capacity for good. Returns how many were deleted."""
    storage = storage or get_storage()
    cutoff = timezone.now() - timedelta(hours=abandoned_file_age_hours())
    count = 0
    stale = TransferFile.objects.filter(
        transfer__kind=TransferKind.REQUEST, uploaded=False, created_at__lte=cutoff
    ).select_related('transfer')
    for file in stale:
        try:
            if file.upload_id:
                storage.abort_multipart_upload(file.storage_key, file.upload_id)
            if TransferFile.objects.filter(pk=file.pk, uploaded=False).delete()[0]:
                count += 1
        except Exception:
            logger.exception('Could not delete the abandoned upload %s', file.pk)
    return count
