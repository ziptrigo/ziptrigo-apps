"""What the receipts API endpoints share: ownership lookups, the mapping from service errors to
HTTP statuses, and the filter/pagination parameters of the list and export endpoints.

Status codes (documented once, here):

- **400** a `ValidationError` from a service (a bad field, an unsupported or oversized file, the
  daily file cap, ...), or one of the few query parameters parsed by hand: `date_from`/`date_to`
  and `disposition`.
- **422** django-ninja's own validation, before an endpoint runs, with a *list-valued* `detail`
  (`[{"loc": ..., "msg": ...}]`, unlike every other error's string): a malformed id in the path
  (`/receipts/not-a-uuid`), a mistyped query parameter (`limit=abc`, `duplicates=maybe`), a body
  field of the wrong type, and an unknown key in a `PATCH` body.
- **402** `AccountFrozenError` (storage unpaid: uploads, re-runs, previews and downloads are
  refused until the balance is topped up) and `InsufficientCreditsError` (the balance can't cover
  a re-run). One code for both, like the file transfer API: the request is well-formed, the
  account just can't pay for it right now.
- **404** a receipt, upload or category that isn't the caller's (never 403, so an id can't be
  probed).
- **429** the site-wide rate limiter (`ratelimit.enforce`), with `Retry-After`.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from apps.billing.services import InsufficientCreditsError

from ..models import Receipt, ReceiptStatus
from ..services import AccountFrozenError
from ..services.filters import DEFAULT_SORT, ReceiptFilters

#: Page size bounds of the list endpoint (clamped, not rejected).
DEFAULT_LIMIT = 20
MAX_LIMIT = 100

#: A `status=` shorthand for everything the pipeline still owns, like the dashboard's Processing
#: tab (`received`, `processing`, `waiting_for_credits`).
IN_PROGRESS = 'in_progress'
_IN_PROGRESS_STATUSES = (
    ReceiptStatus.RECEIVED,
    ReceiptStatus.PROCESSING,
    ReceiptStatus.WAITING_FOR_CREDITS,
)


def validation_detail(exc: ValidationError) -> str:
    """One readable line for a `ValidationError`: the first message, or `field: message` pairs
    when the service keyed its errors by field (a bad `PATCH`)."""
    if hasattr(exc, 'error_dict'):
        return ' '.join(
            f'{field}: {message}'
            for field, messages in exc.message_dict.items()
            for message in messages
        )
    return exc.messages[0]


@contextmanager
def mapped_errors() -> Iterator[None]:
    """Turn the services' exceptions into the API's statuses (400 / 402, see the module docs)."""
    try:
        yield
    except AccountFrozenError as exc:
        raise HttpError(402, exc.message) from exc
    except InsufficientCreditsError as exc:
        raise HttpError(402, str(exc) or 'Insufficient credits') from exc
    except ValidationError as exc:
        raise HttpError(400, validation_detail(exc)) from exc


def owned_receipt(request, receipt_id, *, include_unfinished: bool = False) -> Receipt:
    """The caller's receipt (live or trashed), or a 404. A direct upload that has not completed
    (`uploaded=False`) is not a receipt yet: only the `complete` endpoint asks for those."""
    queryset = Receipt.objects.select_related('category')
    if not include_unfinished:
        queryset = queryset.filter(uploaded=True)
    return get_object_or_404(queryset, id=receipt_id, user=request.auth)


def clamp_page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, MAX_LIMIT)), max(0, offset)


def _parse_date(name: str, value: str | None) -> date | None:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise HttpError(400, f'{name} must be a date like 2026-10-09.') from None


def receipt_filters(
    *,
    status: str | None,
    category: str | None,
    q: str,
    date_from: str | None,
    date_to: str | None,
    sort: str,
    duplicates: bool,
) -> ReceiptFilters:
    """The list and export filters, as `ReceiptFilters` (the same definition the dashboard uses).
    `status` is one status or several, comma-separated (`in_progress` stands for the three
    in-flight ones); `category` is an id or `none`; the dates bound `receipt_date`, inclusive."""
    statuses: list[str] | None = None
    if status:
        statuses = []
        for part in (p.strip() for p in status.split(',')):
            statuses.extend(_IN_PROGRESS_STATUSES if part == IN_PROGRESS else [part])
    return ReceiptFilters(
        status=statuses,
        category=category or None,
        q=q,
        date_from=_parse_date('date_from', date_from),
        date_to=_parse_date('date_to', date_to),
        duplicates=duplicates,
        sort=sort or DEFAULT_SORT,
    )
