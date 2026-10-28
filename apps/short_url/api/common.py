"""What the Short URLs API endpoints share: the ownership lookup, the mapping from service
errors to HTTP statuses, and how a link is prepared for its schema.

Status codes (documented once, here):

- **400** a `ValidationError` from a service (a bad field, the cap on links, a link that can no
  longer be changed, ...), or one of the few hand-parsed query parameters (`status`, `sort`,
  `range`). The `detail` is `field: message` for a field's error.
- **402** `InsufficientCreditsError`: the balance can't cover what the request would charge. The
  request is well-formed, the account just can't pay for it right now (as in the other APIs).
- **403** the destination's host is on the site's block list (`BlockedDomainError`; the detail is
  the neutral "That address cannot be used.", never why), or the account is not active
  (`OwnerInactiveError`).
- **404** a link that isn't the caller's (never 403, so an id can't be probed) or that the caller
  deleted.
- **409** the alias is taken (`AliasTakenError`: in use, reserved or once someone's, never saying
  whose), the link is held for abuse review so its owner may only disable it (`LinkHeldError`), or
  the cost is not what the caller's `expected_cost` said (`CostChangedError`; nothing was charged
  or changed, and the detail names the real total).
- **422** django-ninja's own validation, before an endpoint runs, with a *list-valued* `detail`
  (`[{"loc": ..., "msg": ...}]`): a malformed id in the path, a mistyped query parameter or body
  field, an unknown key in a body.
- **429** the site-wide rate limiter (`ratelimit.enforce`), with `Retry-After`. The rules and
  budgets are the web's (`SU_CREATE_USER` for a create, `SU_EDIT_USER` for a `PATCH`,
  `SU_QR_USER` for the QR image), so a client and the website share them. `SU_QUOTE_USER` (loose)
  is charged by `POST /links/quote` and by every `POST /links`, before its checks: a refused
  create spends that and not `SU_CREATE_USER`. A `PATCH` is checked (`check_update`) before it
  spends `SU_EDIT_USER`, apart from a changed cost (409), which is only found inside the
  transaction and does spend one edit.

**Costs.** Creating and following a link is free; the extras (alias, password, detailed
analytics, an active link beyond the free allowance) cost credits once per link. The web asks the
user to confirm the total first. The API has no one to ask, so it accepts whatever a request
costs *unless the client says what it expects*: `expected_cost` (body of a create or a `PATCH`,
query of `enable` and `analytics`) makes the call refuse with a 409 when the real total, worked
out inside the charging transaction, differs. Every write reports `credits_spent`, the single
link endpoints report what the next paid actions would cost (`costs`, `null` for an action the
link's state would refuse), and `POST /links/quote` prices a create without making it.
"""

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja.errors import HttpError

from apps.billing.services import InsufficientCreditsError

from ..models import LinkStatus, ShortLink, ShortUrlSettings
from ..services import (
    AliasTakenError,
    BlockedDomainError,
    CostChangedError,
    LinkHeldError,
    OwnerInactiveError,
    blocklist,
    destination_host,
    display_state,
    quote_analytics,
    quote_enable,
    quote_update,
)
from ..services import billing as billing_service

#: Page size bounds of the list endpoint (clamped, not rejected).
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def validation_detail(exc: ValidationError) -> str:
    """One readable line for a `ValidationError`: the first message, or `field: message` pairs
    when the service keyed its errors by field."""
    if hasattr(exc, 'error_dict'):
        return ' '.join(
            f'{field}: {message}'
            for field, messages in exc.message_dict.items()
            for message in messages
        )
    return exc.messages[0]


def _cost_text(total: int) -> str:
    price = 'free' if total == 0 else f'{total} credit{"" if total == 1 else "s"}'
    return (
        f'The cost changed: it is now {price}. Send expected_cost={total} to confirm it, '
        'or leave expected_cost out to accept any cost.'
    )


@contextmanager
def mapped_errors() -> Generator[None]:
    """Turn the services' exceptions into the API's statuses (see the module docs). The specific
    `ValidationError` subclasses come first: each has a status of its own."""
    try:
        yield
    except InsufficientCreditsError as exc:
        raise HttpError(402, str(exc) or 'Insufficient credits') from exc
    except (BlockedDomainError, OwnerInactiveError) as exc:
        raise HttpError(403, validation_detail(exc)) from exc
    except (AliasTakenError, LinkHeldError) as exc:
        raise HttpError(409, validation_detail(exc)) from exc
    except CostChangedError as exc:
        raise HttpError(409, _cost_text(exc.quote.total)) from exc
    except ValidationError as exc:
        raise HttpError(400, validation_detail(exc)) from exc


def owned_link(request, link_id: UUID) -> ShortLink:
    """The caller's link, or a 404 (a link they deleted is gone for them too)."""
    queryset = ShortLink.objects.select_related('owner').exclude(status=LinkStatus.DELETED)
    return get_object_or_404(queryset, pk=link_id, owner=request.auth)


def clamp_page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, MAX_LIMIT)), max(0, offset)


def prepare(links: Iterable[ShortLink], *, now: datetime | None = None) -> list[ShortLink]:
    """Set what `LinkSchema` reads but the database doesn't hold on each link: its display
    `state` and whether its detailed statistics are open. One query for the block list for the
    whole page, and the settings read once."""
    now = now or timezone.now()
    links = list(links)
    settings_row = ShortUrlSettings.load()
    analytics_is_free = billing_service.price(billing_service.Extra.ANALYTICS, settings_row) == 0
    blocked = blocklist.blocked_hosts(destination_host(link.destination) for link in links)
    for link in links:
        link.state = display_state(
            link, now=now, destination_blocked=destination_host(link.destination) in blocked
        )
        link.analytics_open = analytics_is_free or link.analytics_paid_at is not None
    return links


def detail(link: ShortLink) -> ShortLink:
    """`prepare` for one link, plus what its next paid actions would cost (`CostsSchema`): `None`
    for an action the link's state would refuse, whatever it costs."""
    (link,) = prepare([link])
    editable = link.status in (LinkStatus.ACTIVE, LinkStatus.DISABLED)
    # `link.state` is `blocked` for a disabled link whose destination is on the block list.
    can_enable = (
        link.status == LinkStatus.DISABLED
        and link.held_for_review_at is None
        and link.state != 'blocked'
    )
    link.costs = {
        'enable': quote_enable(link).total if can_enable else None,
        'analytics': quote_analytics(link).total if editable else None,
        'password': (
            quote_update(link, password='x').total
            if editable and link.held_for_review_at is None
            else None
        ),
    }
    return link
