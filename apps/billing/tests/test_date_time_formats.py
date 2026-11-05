"""The credits history shows each time in the account's clock (issue #245)."""

import re

import pytest
from django.urls import reverse

from apps.accounts.services.display_formats import set_display_formats
from apps.billing.services import add_credits

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.mark.parametrize(
    ('time_format', 'pattern'),
    [
        ('12h', r'\d{4}-\d{2}-\d{2} \d{1,2}:\d{2} (AM|PM) UTC</time>'),
        ('24h', r'\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC</time>'),
    ],
)
def test_the_history_time_follows_the_clock_and_keeps_an_iso_date(
    client, user, time_format, pattern
):
    add_credits(user, 5, description='Seed', source='test')
    set_display_formats(user, time_format=time_format)
    client.force_login(user)

    body = client.get(reverse('billing:credits-history')).content.decode()

    assert re.search(pattern, body), body
    assert f'data-time-format="{time_format}"' in body
