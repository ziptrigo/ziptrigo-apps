"""The link endpoints (`/api/s/links`): list, create (and price a create), read, edit, enable,
disable, delete, open the detailed analytics, the QR image and the owner's preview.

Every endpoint calls the same `apps.short_url.services` functions the web views do, so the same
validation, billing and abuse rules apply either way. Another user's link is a 404. See
`common.py` for the status codes and for how costs work for a client with no one to confirm with.
"""

from uuid import UUID

from django.http import HttpResponse
from ninja.errors import HttpError

from apps.billing.services import get_balance
from apps.core import ratelimit
from apps.core.services.qr_image import render_png

from .. import services
from ..schemas import (
    LinkCreateSchema,
    LinkDetailSchema,
    LinkListSchema,
    LinkUpdateSchema,
    PreviewSchema,
    QuoteSchema,
)
from ..services import UNSET, LinkInput, destination_host, is_available, short_url
from ..services.destinations import UTM_PARAMS
from ..services.filters import DEFAULT_SORT, SORTS, STATES, filter_links
from .common import DEFAULT_LIMIT, clamp_page, detail, mapped_errors, owned_link, prepare
from .router import router


def _input(payload: LinkCreateSchema) -> LinkInput:
    return LinkInput(
        destination=payload.destination,
        alias=payload.alias,
        title=payload.title,
        tags=payload.tags,
        expires_at=payload.expires_at,
        max_clicks=payload.max_clicks,
        utm={name: getattr(payload, name) for name in UTM_PARAMS},
        password=payload.password,
        filter_bots=payload.filter_bots,
    )


@router.get('/links', response=LinkListSchema)
def list_links(
    request,
    q: str = '',
    tag: str = '',
    status: str = 'all',
    sort: str = DEFAULT_SORT,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
):
    """List the caller's links (not the deleted ones), newest first by default.

    - `q`: matches the code or alias, the destination, the title and the tags.
    - `tag`: only links with this tag.
    - `status`: `all` (default), `active`, `disabled`, `expired`, `capped` (past `max_clicks`) or
      `taken_down`: the states of `LinkSchema.state`, as the dashboard's tabs.
    - `sort`: `-created_at` (default), `created_at`, `-click_count` or `click_count`.
    - `limit` (1-100, default 20) and `offset` page the result; `count` is the total.
    """
    if status != 'all' and status not in STATES:
        raise HttpError(400, f'status must be one of: all, {", ".join(STATES)}.')
    if sort not in SORTS:
        raise HttpError(400, f'sort must be one of: {", ".join(SORTS)}.')
    limit, offset = clamp_page(limit, offset)
    queryset = filter_links(
        request.auth, tab=status, q=q.strip()[:200], tag=tag.strip().lower(), sort=sort
    )
    return {
        'count': queryset.count(),
        'limit': limit,
        'offset': offset,
        'results': prepare(queryset[offset : offset + limit]),
    }


@router.post('/links', response={201: LinkDetailSchema})
def create_link(request, payload: LinkCreateSchema):
    """Create a link. The response's `credits_spent` says what it cost.

    A random 7-character code is issued unless `alias` is given (3-32 letters, digits, `-` or
    `_`, case-insensitive; a paid extra, and fixed for good). `title` is optional: without one,
    the destination page's title is fetched in the background. `password` makes visitors enter it
    before the redirect (a paid extra). `expires_at` and `max_clicks` end the link; `filter_bots`
    (default true) leaves crawlers and link previews out of the click counts.

    A refused create (400/403/409) spends none of the `SU_CREATE_USER` budget (only the much
    looser `SU_QUOTE_USER`, shared with `POST /links/quote`). 402 if the balance can't cover it;
    409 if `expected_cost` is sent and differs from what it would charge.
    """
    data = _input(payload)
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'SU_QUOTE_USER'))
    with mapped_errors():
        # Refuse a bad request before it spends any of the rate-limit budget, like the web form.
        services.check_new_link(request.auth, data)
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'SU_CREATE_USER'))
    with mapped_errors():
        link = services.create_link(request.auth, data, expected_cost=payload.expected_cost)
    return 201, detail(link)


@router.post('/links/quote', response=QuoteSchema)
def quote_link(request, payload: LinkCreateSchema):
    """What `POST /links` would charge for this body, without creating anything. The body is
    checked like a real create (so you learn about a taken alias or a bad destination now) and
    `affordable` compares the total with your balance. The `credits` to send as `expected_cost`.
    Costs nothing but is limited per user (`SU_QUOTE_USER`, a loose rule)."""
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'SU_QUOTE_USER'))
    with mapped_errors():
        quote = services.check_new_link(request.auth, _input(payload))
    balance = get_balance(request.auth)
    return {
        'credits': quote.total,
        'charges': list(quote.charges),
        'balance': balance,
        'affordable': balance >= quote.total,
    }


@router.get('/links/{link_id}', response=LinkDetailSchema)
def get_link(request, link_id: UUID):
    """One link (a taken-down link too, with its `takedown_reason`), with what its next paid
    actions would cost."""
    return detail(owned_link(request, link_id))


@router.patch('/links/{link_id}', response=LinkDetailSchema)
def update_link(request, link_id: UUID, payload: LinkUpdateSchema):
    """Edit a link. Only the fields present in the body are applied, all or nothing; the code
    never changes, so redirects follow the new destination at once (they are `302`s).

    Setting a password on a link that never had one costs credits once (`credits_spent`;
    `expected_cost` guards it). A link under review can only be disabled (409), and one that was
    taken down can't be changed (400). A body with no field changes nothing and spends no budget.

    What can be refused without a transaction (a link that isn't editable, a bad field, a blocked
    destination) is refused *before* `SU_EDIT_USER` is charged, so it spends none of it; a 409
    for a changed cost is only found inside the transaction and does spend one edit.
    """
    link = owned_link(request, link_id)
    changes = payload.model_dump(exclude_unset=True)
    expected_cost = changes.pop('expected_cost', None)
    utm = {name: changes.pop(name) for name in UTM_PARAMS if name in changes}
    if not changes and not utm:
        link.credits_spent = 0
        return detail(link)
    fields = {
        'destination': changes.get('destination', UNSET),
        'utm': utm or None,
        'title': changes.get('title', UNSET),
        'tags': changes.get('tags', UNSET),
        'expires_at': changes.get('expires_at', UNSET),
        'max_clicks': changes.get('max_clicks', UNSET),
        'password': changes.get('password', UNSET),
    }
    with mapped_errors():
        # Refuse what needs no transaction before it spends any of the edit budget.
        services.check_update(link, **fields)
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'SU_EDIT_USER'))
    with mapped_errors():
        services.update_link(
            link,
            **fields,
            filter_bots=changes.get('filter_bots', UNSET),
            expected_cost=expected_cost,
        )
    return detail(link)


@router.post('/links/{link_id}/enable', response=LinkDetailSchema)
def enable_link(request, link_id: UUID, expected_cost: int | None = None):
    """Enable a disabled link. An active link beyond the free allowance costs credits once per
    link (`credits_spent`; re-enabling one that already paid is free; `expected_cost` guards
    it). Idempotent: an already active link changes nothing. A link whose destination is on the
    block list can't be enabled (403)."""
    link = owned_link(request, link_id)
    with mapped_errors():
        services.set_enabled(link, True, expected_cost=expected_cost)
    return detail(link)


@router.post('/links/{link_id}/disable', response=LinkDetailSchema)
def disable_link(request, link_id: UUID):
    """Disable a link: it shows the neutral unavailable page until enabled again. Always free,
    and still possible while the link is under review. Idempotent."""
    link = owned_link(request, link_id)
    with mapped_errors():
        services.set_enabled(link, False)
    return detail(link)


@router.delete('/links/{link_id}', response={204: None})
def delete_link(request, link_id: UUID):
    """Delete a link: it stops working and leaves your lists, but its code is never issued again
    (an old printed link can't be taken over). A deleted link is a 404 from then on. A link under
    review (409) or taken down (400) can't be deleted."""
    link = owned_link(request, link_id)
    with mapped_errors():
        services.delete_link(link)
    return 204, None


@router.post('/links/{link_id}/analytics', response=LinkDetailSchema)
def enable_analytics(request, link_id: UUID, expected_cost: int | None = None):
    """Open the link's detailed statistics (`GET /links/{id}/stats` `detail`): costs credits once
    per link (`credits_spent`; `expected_cost` guards it); free when already open. Events are
    recorded from the first click, so the history is there at once."""
    link = owned_link(request, link_id)
    with mapped_errors():
        services.enable_analytics(link, expected_cost=expected_cost)
    return detail(link)


@router.get('/links/{link_id}/qr.png')
def link_qr(request, link_id: UUID) -> HttpResponse:
    """The short link as a QR code (`image/png`), rendered on demand. Limited per user
    (`SU_QR_USER`, shared with the web page)."""
    link = owned_link(request, link_id)
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'SU_QR_USER'))
    response = HttpResponse(render_png(short_url(link.code)), content_type='image/png')
    # What it encodes can't change (a code is permanent), so a client may keep it a while.
    response['Cache-Control'] = 'private, max-age=3600'
    return response


@router.get('/links/{link_id}/preview', response=PreviewSchema)
def link_preview(request, link_id: UUID):
    """What the public preview page (`/s/<code>+`) shows, for your own link: the destination and
    its host, the title and when it was made, whether it is password protected and whether it
    redirects right now (`available`)."""
    link = owned_link(request, link_id)
    return {
        'short_url': short_url(link.code),
        'preview_url': f'{short_url(link.code)}+',
        'destination': link.destination,
        'host': destination_host(link.destination),
        'title': link.title,
        'created_at': link.created_at,
        'password_protected': bool(link.password_hash),
        'available': is_available(link),
    }
