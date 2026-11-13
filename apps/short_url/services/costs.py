"""What Short URLs cost, for the Costs page (`apps.core.costs`)."""

from apps.accounts.models import User
from apps.core.costs import CostSection, charge, free

from ..models import ShortUrlSettings
from .limits import unpaid_active_link_count


def cost_section(user: User) -> CostSection:
    config = ShortUrlSettings.load()
    allowance = config.free_active_links
    lines = [
        free(
            'Create and follow links',
            f'Up to {allowance:,} active links. Click counts included.',
        ),
        charge(f'Each active link beyond {allowance:,}', config.extra_link_price, 'once per link'),
        charge(
            'Custom alias',
            config.custom_alias_price,
            'once per link',
            'Choose the code yourself, like /s/spring-sale.',
        ),
        charge('Password', config.password_price, 'once per link'),
        charge(
            'Detailed statistics',
            config.analytics_price,
            'once per link',
            'Countries, referrers, devices and browsers.',
        ),
    ]

    # The free allowance counts the active links that never paid the extra-link price
    # (`limits.is_extra_link`); a link that paid keeps its place for good.
    in_use = min(unpaid_active_link_count(user), allowance)
    usage = [f'{in_use:,} of your {allowance:,} free active links are in use.']

    return CostSection(
        lines=lines,
        usage=usage,
        footnote='You confirm the cost of a paid extra first. No recurring charges: a link keeps '
        'every extra it paid for, also when it is disabled and enabled again.',
    )
