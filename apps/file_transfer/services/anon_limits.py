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
from django.db.models import QuerySet, Sum
from django.utils import timezone

from ..models import FileTransferSettings, Transfer, TransferFile, TransferStatus

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


def _candidate_values(*values: str | None) -> list[str]:
    """Dedupe a handful of candidate IP/cookie values, dropping blanks and `None` -- shared by
    `check_send_caps` to build its "own recorded identifier, plus whatever the confirming request
    presents" candidate list (see that function's docstring)."""
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


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
    `cleanup_drafts` job is what eventually reclaims an abandoned draft's bytes).

    Sums the real `TransferFile.size` of every file attached to another in-flight transfer, rather
    than trusting that transfer's own `size_bytes` column: `size_bytes` isn't populated until
    `_activate`/`finalize_send` runs, so a draft or still-pending transfer always reports `0`
    there regardless of how much it's actually uploaded -- reading it here would let a sender
    upload arbitrarily many bytes across any number of never-confirmed drafts/sessions with no
    real bound.
    """
    settings_row = settings_row or FileTransferSettings.load()
    since = timezone.now() - _WINDOW
    by_ip, by_cookie = _by_ip_and_cookie(ip, cookie_id, transfer, since)

    this_transfer_so_far = sum(f.size for f in transfer.files.all())

    def total(qs: QuerySet) -> int:
        other_ids = qs.filter(status__in=_OCCUPIES_STORAGE).values_list('pk', flat=True)
        other = (
            TransferFile.objects.filter(transfer_id__in=other_ids).aggregate(total=Sum('size'))[
                'total'
            ]
            or 0
        )
        return other + this_transfer_so_far + additional_bytes

    if max(total(by_ip), total(by_cookie)) > settings_row.anonymous_max_bytes_per_ip_per_day:
        raise ValidationError(_LIMIT_MESSAGE)


def check_send_caps(
    transfer: Transfer,
    ip: str | None,
    cookie_id: str,
    settings_row: FileTransferSettings | None = None,
) -> None:
    """The authoritative per-IP-per-day check (spec section 13): both the transfer-count cap and
    the byte cap, against every other anonymous transfer that was ever actually confirmed
    (`completed_at` set) in the last 24h from any of this transfer's own candidate identifiers.

    Checked against *every* IP/cookie this transfer has ever presented -- the one recorded when
    its draft was created (`transfer.sender_ip`/`.anon_cookie_id`) as well as whatever the
    confirming request itself presents (`ip`/`cookie_id`) -- taking whichever single one reports
    the highest count/bytes. Confirming from a different network or browser than the one that
    uploaded the files is completely legitimate (spec section 2: "the sender opens their email on
    their phone"), so checking only the confirming request's own IP/cookie would let anyone
    launder unlimited anonymous transfers through one connection by simply confirming each one
    from a fresh network/browser with no history of its own -- the caps would then see zero usage
    for every single confirmation.

    Call after `transfer.size_bytes` has been set to the real uploaded total (mirrors
    `services.send.finalize_send`), and before flipping the transfer to `ACTIVE`. `start_confirmation`
    also calls this, as a pre-check before a code/link is even sent -- but this call, at actual
    confirmation, remains the authoritative one: the pre-check can go stale between the two.
    """
    settings_row = settings_row or FileTransferSettings.load()
    since = timezone.now() - _WINDOW
    base = Transfer.objects.filter(
        owner__isnull=True, created_at__gte=since, completed_at__isnull=False
    ).exclude(pk=transfer.pk)

    ips = _candidate_values(ip, transfer.sender_ip)
    cookies = _candidate_values(cookie_id, transfer.anon_cookie_id)

    counts = [base.filter(sender_ip=value).count() for value in ips]
    counts += [base.filter(anon_cookie_id=value).count() for value in cookies]
    if counts and max(counts) >= settings_row.anonymous_max_transfers_per_ip_per_day:
        raise ValidationError(_LIMIT_MESSAGE)

    byte_totals = [
        sum(t.size_bytes for t in base.filter(sender_ip=value)) + transfer.size_bytes
        for value in ips
    ]
    byte_totals += [
        sum(t.size_bytes for t in base.filter(anon_cookie_id=value)) + transfer.size_bytes
        for value in cookies
    ]
    if byte_totals and max(byte_totals) > settings_row.anonymous_max_bytes_per_ip_per_day:
        raise ValidationError(_LIMIT_MESSAGE)
