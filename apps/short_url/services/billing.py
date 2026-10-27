"""The one-off prices of a link's extras, charged through `apps.billing.services`.

Creating and following a link is free. Each extra costs a number of credits **once per link**
(`ShortUrlSettings`; 0 makes it free), and so does every active link beyond the free allowance.
What was paid for is recorded on the link (`alias_paid_at`, ...): turning an extra off and on
again, or re-enabling a link that paid its way in, costs nothing more. There are no refunds.

`charge_extra` is the only place credits are spent; it raises `InsufficientCreditsError`
(`apps.billing.services`), which the web shows as a 422 with a Top up link and the API will answer
as a 402. Which extras this phase charges: the custom alias and the extra link. The password and
detailed analytics prices exist in the settings and in `Extra`, and are wired in with those
features.
"""

from dataclasses import dataclass
from enum import StrEnum

from django.db import transaction
from django.utils import timezone

from apps.billing.services import spend_credits

from ..models import ShortLink, ShortUrlSettings

SOURCE = 'short_url'


class Extra(StrEnum):
    ALIAS = 'alias'
    PASSWORD = 'password'
    ANALYTICS = 'analytics'
    EXTRA_LINK = 'extra_link'


#: The `ShortLink` column that records each extra as paid.
PAID_FIELD = {
    Extra.ALIAS: 'alias_paid_at',
    Extra.PASSWORD: 'password_paid_at',
    Extra.ANALYTICS: 'analytics_paid_at',
    Extra.EXTRA_LINK: 'extra_link_paid_at',
}

_PRICE_FIELD = {
    Extra.ALIAS: 'custom_alias_price',
    Extra.PASSWORD: 'password_price',
    Extra.ANALYTICS: 'analytics_price',
    Extra.EXTRA_LINK: 'extra_link_price',
}

LABELS = {
    Extra.ALIAS: 'Custom alias',
    Extra.PASSWORD: 'Password',
    Extra.ANALYTICS: 'Detailed analytics',
    Extra.EXTRA_LINK: 'Extra active link',
}


@dataclass(frozen=True, slots=True)
class Charge:
    extra: Extra
    credits: int

    @property
    def label(self) -> str:
        return LABELS[self.extra]


@dataclass(frozen=True, slots=True)
class Quote:
    """What an action would charge: only the extras that cost something."""

    charges: tuple[Charge, ...] = ()

    @property
    def total(self) -> int:
        return sum(charge.credits for charge in self.charges)

    def __bool__(self) -> bool:
        return self.total > 0

    @property
    def summary(self) -> str:
        """`Custom alias 1, Extra active link 1`, for a confirmation message."""
        return ', '.join(f'{charge.label.lower()} {charge.credits}' for charge in self.charges)


def price(extra: Extra, settings: ShortUrlSettings | None = None) -> int:
    settings = settings or ShortUrlSettings.load()
    return getattr(settings, _PRICE_FIELD[extra])


def is_paid(link: ShortLink, extra: Extra) -> bool:
    return getattr(link, PAID_FIELD[extra]) is not None


def quote(
    extras: list[Extra] | tuple[Extra, ...], settings: ShortUrlSettings | None = None
) -> Quote:
    """The charge for each of `extras` that has a price. The caller decides which extras an
    action needs (and leaves out those a link already paid for)."""
    settings = settings or ShortUrlSettings.load()
    charges = [Charge(extra, price(extra, settings)) for extra in extras]
    return Quote(tuple(charge for charge in charges if charge.credits > 0))


def charge_extra(link: ShortLink, extra: Extra, *, settings: ShortUrlSettings | None = None) -> int:
    """Pay for `extra` on `link`, once. Returns the credits spent: 0 when it was already paid
    for (or costs nothing, in which case it is still marked as paid).

    The link's row is locked and the `*_paid_at` column claimed with a compare-and-swap in the
    same transaction as the spend, so two requests can't both pay, and a failed spend
    (`InsufficientCreditsError`) rolls the claim back. Call it inside the transaction that makes
    the extra take effect, so a refusal leaves nothing half done.
    """
    field = PAID_FIELD[extra]
    amount = price(extra, settings)
    now = timezone.now()
    with transaction.atomic():
        ShortLink.objects.select_for_update().filter(pk=link.pk).first()
        claimed = ShortLink.objects.filter(pk=link.pk, **{f'{field}__isnull': True}).update(
            **{field: now}
        )
        if not claimed:
            return 0
        if amount > 0:
            spend_credits(
                link.owner,
                amount,
                description=f'Short URL: {LABELS[extra].lower()} for /s/{link.code}',
                source=SOURCE,
            )
    setattr(link, field, now)
    return amount
