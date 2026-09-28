"""Per-IP-per-day caps on anonymous sending (spec section 13): count of transfers and total bytes,
checked against *whichever* of the real client IP or a signed browser cookie reports the higher
usage -- so neither clearing cookies nor sending from behind a shared/rotating IP alone raises the
effective limit.

Both counters use a rolling 24h window (`created_at >= now - 24h`), the same style the rest of
this app's "daily" limits use (see `services.metering`'s billing period) rather than a calendar
day, which would let a sender burn a whole day's quota at 23:59 and another right after midnight.

Two checks, at two different points in the flow (a deliberate design decision -- see each
function's docstring for why one check alone isn't enough):

- `check_upload_bytes_cap`: at every file upload (`services.uploads.add_file`), so a sender can't
  blow past the byte cap just by never confirming (uploads to S3 cost storage the moment they
  land, whether or not the transfer is ever confirmed).
- `check_send_caps`: at confirmation (`services.anonymous.confirm_*`), the one point where a
  transfer actually *counts* -- this is the authoritative check for both the transfer-count cap
  and the byte cap, since only a transfer that ends up confirmed should count against "how many
  anonymous transfers did this IP send today".
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.utils import timezone

from ..models import FileTransferSettings, Transfer, TransferStatus

#: Rolling window for both caps -- see the module docstring for why not a calendar day.
_WINDOW = timedelta(hours=24)

#: Statuses whose `size_bytes` still occupies storage right now, and so still counts against the
#: upload-time byte cap: everything except a transfer whose objects are already gone (`DELETED`,
#: or an `EXPIRED`/`SUSPENDED` transfer that has finished `files_deleted_at`). Checked in addition
#: to (rather than instead of) the transfer's own uploaded files, which aren't reflected in
#: `size_bytes` until `finalize_send`/`confirm_*` runs.
_OCCUPIES_STORAGE = (
    TransferStatus.DRAFT,
    TransferStatus.PENDING_CONFIRMATION,
    TransferStatus.ACTIVE,
    TransferStatus.DISABLED,
    TransferStatus.SUSPENDED,
    TransferStatus.EXPIRED,
)

_LIMIT_MESSAGE = (
    'Daily limit reached for anonymous transfers from this connection. Try again tomorrow, or '
    'sign in to send without a limit.'
)


def _by_ip_and_cookie(
    ip: str | None, cookie_id: str, transfer: Transfer, since
) -> tuple[QuerySet, QuerySet]:
    base = Transfer.objects.filter(owner__isnull=True, created_at__gte=since).exclude(
        pk=transfer.pk
    )
    by_ip = base.filter(sender_ip=ip) if ip else Transfer.objects.none()
    by_cookie = base.filter(anon_cookie_id=cookie_id) if cookie_id else Transfer.objects.none()
    return by_ip, by_cookie


def check_upload_bytes_cap(
    transfer: Transfer,
    additional_bytes: int,
    ip: str | None,
    cookie_id: str,
    settings_row: FileTransferSettings | None = None,
) -> None:
    """Reject a file upload that would push this connection's (still in-flight) daily byte usage
    over the cap -- checked in addition to the authoritative `check_send_caps` at confirmation, so
    storage can't be exhausted by uploading without ever confirming (spec section 13; the 24h
    `cleanup_drafts` job is what eventually reclaims an abandoned draft's bytes)."""
    settings_row = settings_row or FileTransferSettings.load()
    since = timezone.now() - _WINDOW
    by_ip, by_cookie = _by_ip_and_cookie(ip, cookie_id, transfer, since)

    this_transfer_so_far = sum(f.size for f in transfer.files.all())

    def total(qs: QuerySet) -> int:
        other = sum(t.size_bytes for t in qs.filter(status__in=_OCCUPIES_STORAGE))
        return other + this_transfer_so_far + additional_bytes

    if max(total(by_ip), total(by_cookie)) > settings_row.anonymous_max_bytes_per_ip_per_day:
        raise ValidationError(_LIMIT_MESSAGE)


def check_send_caps(
    transfer: Transfer,
    ip: str | None,
    cookie_id: str,
    settings_row: FileTransferSettings | None = None,
) -> None:
    """The authoritative per-IP-per-day check, run once at confirmation (spec section 13): both
    the transfer-count cap and the byte cap, against every other anonymous transfer from this
    IP/cookie that was ever actually confirmed (`completed_at` set) in the last 24h.

    Call after `transfer.size_bytes` has been set to the real uploaded total (mirrors
    `services.send.finalize_send`), and before flipping the transfer to `ACTIVE`.
    """
    settings_row = settings_row or FileTransferSettings.load()
    since = timezone.now() - _WINDOW
    by_ip, by_cookie = _by_ip_and_cookie(ip, cookie_id, transfer, since)
    sent_ip = by_ip.filter(completed_at__isnull=False)
    sent_cookie = by_cookie.filter(completed_at__isnull=False)

    if (
        max(sent_ip.count(), sent_cookie.count())
        >= settings_row.anonymous_max_transfers_per_ip_per_day
    ):
        raise ValidationError(_LIMIT_MESSAGE)

    bytes_ip = sum(t.size_bytes for t in sent_ip) + transfer.size_bytes
    bytes_cookie = sum(t.size_bytes for t in sent_cookie) + transfer.size_bytes
    if max(bytes_ip, bytes_cookie) > settings_row.anonymous_max_bytes_per_ip_per_day:
        raise ValidationError(_LIMIT_MESSAGE)
