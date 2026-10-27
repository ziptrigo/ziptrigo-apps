"""Schemas for the link endpoints (`/api/s/links`): one link, a page of them, the create and
partial-update bodies, the quote and the owner's preview."""

import re
from datetime import date, datetime, time
from typing import Annotated, Any
from uuid import UUID

from django.utils import timezone
from ninja import Schema
from pydantic import BeforeValidator, ConfigDict, Field

from ..services import short_url as build_short_url

# Generous ceilings, so an absurd body is a 422 before any service runs; the services enforce the
# real limits (2,048 characters for a destination, 255 for a title, ...) with their own messages.
_DESTINATION_MAX = 4096
_TEXT_MAX = 1000
_UTM_MAX = 200


_DATE_ONLY = re.compile(r'\d{4}-\d{2}-\d{2}')


def _date_only_to_end_of_day(value: Any) -> Any:
    """Read a bare date ("2027-12-31") as the end of that day in the server's time zone, as file
    transfer's API and the web date picker do: pydantic would otherwise read it as midnight at
    the *start* of the day, so the link would stop a day before its owner meant. Anything with a
    time part passes through."""
    if isinstance(value, str) and _DATE_ONLY.fullmatch(value.strip()):
        value = date.fromisoformat(value.strip())
    if isinstance(value, date) and not isinstance(value, datetime):
        return timezone.make_aware(datetime.combine(value, time.max.replace(microsecond=0)))
    return value


ExpiresAt = Annotated[
    datetime | None,
    BeforeValidator(_date_only_to_end_of_day),
    Field(
        description=(
            'An ISO 8601 date-time; one without a UTC offset ("2027-12-31T18:00:00") is read in '
            'the server\'s time zone (settings.TIME_ZONE). A date alone ("2027-12-31") means the '
            'end of that day in that time zone. `null` means no expiry.'
        ),
    ),
]


class LinkSchema(Schema):
    """One short link, as every link endpoint returns it.

    `status` is what the owner or staff did (`active`, `disabled`, `taken_down`; a deleted link is
    gone, a 404). `state` is what the link is doing *now*, most specific first: `taken_down`,
    `disabled`, `held` (under abuse review), `expired`, `capped` (past `max_clicks`), `blocked` (its
    destination's host is on the site's block list) or `active`; only `active` redirects.

    `click_count` is the headline number and what `max_clicks` caps: with `filter_bots` on (the
    default) it counts people only, bots and link previews going to `bot_click_count`; with it off
    it counts everything, `bot_click_count` being how many of those were bots. A link with
    `max_clicks` counts every redirected click, bots included, whatever `filter_bots` says.

    The password is never returned, only whether there is one (`password_protected`).
    `analytics_enabled` says whether the detailed statistics are open (paid for, or free).
    `takedown_reason` is set only for a taken-down link, and is never who reported it.
    """

    id: UUID
    code: str
    short_url: str
    is_alias: bool
    destination: str
    title: str
    tags: list[str]
    status: str
    state: str
    expires_at: datetime | None
    max_clicks: int | None
    password_protected: bool
    filter_bots: bool
    click_count: int
    bot_click_count: int
    last_clicked_at: datetime | None
    analytics_enabled: bool
    takedown_reason: str
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def resolve_short_url(obj) -> str:
        return build_short_url(obj.code)

    @staticmethod
    def resolve_state(obj) -> str:
        return obj.state

    @staticmethod
    def resolve_password_protected(obj) -> bool:
        return bool(obj.password_hash)

    @staticmethod
    def resolve_analytics_enabled(obj) -> bool:
        return obj.analytics_open

    @staticmethod
    def resolve_takedown_reason(obj) -> str:
        return obj.takedown_reason if obj.status == 'taken_down' else ''


class CostsSchema(Schema):
    """What each action that can charge would cost this link right now, in credits (0: free, or
    already paid for), or `null` when the link's state would refuse the action (it is not
    possible, whatever it costs). A client that confirms prices with its user reads these, then
    sends the number back as `expected_cost`."""

    #: Enabling the link (an active link beyond the free allowance, once per link). `null` unless
    #: the link is disabled and could be enabled: an active link has nothing to enable, and one
    #: under review, taken down or pointing at a blocked destination is refused.
    enable: int | None
    #: Opening the detailed statistics (`POST /links/{id}/analytics`, once per link). `null` for
    #: a taken-down link; 0 once open.
    analytics: int | None
    #: Setting a password on a link that never had one (`PATCH` with `password`, once per link).
    #: `null` for a link under review or taken down, which can't be edited.
    password: int | None


class LinkDetailSchema(LinkSchema):
    """A link as the single-link endpoints return it: the link, what its next paid actions would
    cost, and (only on the response to a write) the credits that write spent."""

    costs: CostsSchema
    #: Credits the request just charged: the extras it paid for, the extra-link price. 0 on a read,
    #: on a write that cost nothing and on an action that changed nothing.
    credits_spent: int

    @staticmethod
    def resolve_costs(obj) -> dict:
        return obj.costs

    @staticmethod
    def resolve_credits_spent(obj) -> int:
        return obj.credits_spent


class LinkListSchema(Schema):
    count: int
    limit: int
    offset: int
    results: list[LinkSchema]


class _UtmFields(Schema):
    """The UTM builder: each non-empty value is merged into the destination's query string
    (replacing a parameter of the same name already there). An empty value leaves the address
    alone; to remove a parameter, edit the destination itself."""

    utm_source: str = Field('', max_length=_UTM_MAX)
    utm_medium: str = Field('', max_length=_UTM_MAX)
    utm_campaign: str = Field('', max_length=_UTM_MAX)
    utm_term: str = Field('', max_length=_UTM_MAX)
    utm_content: str = Field('', max_length=_UTM_MAX)


class LinkCreateSchema(_UtmFields):
    """Body for `POST /links` and `POST /links/quote`.

    Only `destination` is required. A custom `alias` costs credits (once, at creation, and it can
    never be changed), and so does a `password`, and so does every active link beyond the free
    allowance: `POST /links/quote` says what this exact body would cost, and `expected_cost`
    makes `POST /links` refuse (409) unless it would charge exactly that. Leave `expected_cost`
    out to accept whatever it costs; the response's `credits_spent` says what was charged.

    An unknown key is a 422 rather than ignored.
    """

    model_config = ConfigDict(extra='forbid')

    destination: str = Field(max_length=_DESTINATION_MAX)
    alias: str = Field('', max_length=64)
    title: str = Field('', max_length=_TEXT_MAX)
    tags: list[str] = Field(default_factory=list, max_length=100)
    expires_at: ExpiresAt = None
    max_clicks: int | None = None
    password: str = Field('', max_length=256)
    filter_bots: bool = True
    expected_cost: int | None = Field(None, ge=0)


class LinkUpdateSchema(_UtmFields):
    """Body for `PATCH /links/{id}`; only the fields present are applied.

    An explicit `null` clears `expires_at`, `max_clicks` and `password` (removing it); the other
    fields can't be null (a 422). `utm_*` values are merged into the destination (the one sent, or
    the link's current one). The alias is fixed at creation, so `alias` is not accepted. Setting a
    password on a link that never had one costs credits once: `expected_cost` (optional) makes
    the call refuse with a 409 unless it would charge exactly that. A key that is not one of these
    fields is a 422, never ignored.
    """

    model_config = ConfigDict(extra='forbid')

    # Defaults only so the fields can be left out (`exclude_unset` tells absent from sent).
    destination: str = Field('', max_length=_DESTINATION_MAX)
    title: str = Field('', max_length=_TEXT_MAX)
    tags: list[str] = Field(default_factory=list, max_length=100)
    expires_at: ExpiresAt = None
    max_clicks: int | None = None
    password: str | None = Field(None, max_length=256)
    filter_bots: bool = True
    expected_cost: int | None = Field(None, ge=0)


class ChargeSchema(Schema):
    extra: str
    label: str
    credits: int


class QuoteSchema(Schema):
    """What `POST /links` would charge for a body, and whether the balance covers it.

    The body was validated like a real create (fields, alias, link cap), so a 4xx here is what the
    create would answer; nothing was saved or charged. `charges` lists only the extras that cost
    something; `credits` is their total (the `expected_cost` to send)."""

    credits: int
    charges: list[ChargeSchema]
    balance: int
    affordable: bool


class PreviewSchema(Schema):
    """What the public preview page (`/s/<code>+`) shows about a link, plus whether it works.

    The public page keeps the destination and title back for a password-protected link until the
    visitor has entered the password; the owner always gets them here. `available` is whether
    `/s/<code>` redirects right now (the same answer for every dead state, which visitors are
    never told apart)."""

    short_url: str
    preview_url: str
    destination: str
    host: str
    title: str
    created_at: datetime
    password_protected: bool
    available: bool
