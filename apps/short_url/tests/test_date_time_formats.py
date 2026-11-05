"""Short URLs and the date and time format (issue #245): the link pages in both formats, the stats
labels, and the Expires date and time fields."""

import re
from datetime import UTC, date, datetime

import pytest
from django.urls import reverse

from apps.accounts.services.display_formats import set_display_formats
from apps.core.datetime_format import DisplayFormats, override

from ..models import ShortLink
from ..services import stats
from .factories import create_link

pytestmark = pytest.mark.django_db

CREATED = datetime(2026, 10, 25, 21, 5, tzinfo=UTC)
BOTH = pytest.mark.parametrize(
    ('date_format', 'time_format', 'created'),
    [('mdy', '12h', '10/25/2026'), ('dmy', '24h', '25/10/2026')],
    ids=['mdy-12h', 'dmy-24h'],
)


@pytest.fixture
def link(funded_user):
    link = create_link(funded_user, code='Fmt1', title='Dated', last_clicked_at=CREATED)
    ShortLink.objects.filter(pk=link.pk).update(created_at=CREATED)
    return link


def flat(response) -> str:
    assert response.status_code == 200
    return re.sub(r'\s+', ' ', response.content.decode())


@BOTH
def test_the_detail_page_and_the_list_follow_the_format(
    web, funded_user, link, date_format, time_format, created
):
    set_display_formats(funded_user, date_format=date_format, time_format=time_format)

    detail = flat(web.get(reverse('short_url:detail', args=[link.pk])))
    listing = flat(web.get(reverse('short_url:dashboard')))

    assert created in detail
    assert created in listing
    # An instant is a `<time>` the script moves to the browser's zone, in the same wording.
    clock = '9:05 PM' if time_format == '12h' else '21:05'
    assert f'{created}, {clock} UTC</time>' in detail


@BOTH
def test_the_expires_fields_are_a_date_field_and_a_time_field_around_a_utc_input(
    web, funded_user, link, date_format, time_format, created
):
    set_display_formats(funded_user, date_format=date_format, time_format=time_format)
    ShortLink.objects.filter(pk=link.pk).update(
        expires_at=datetime(2030, 12, 31, 9, 30, tzinfo=UTC)
    )

    form = flat(web.get(reverse('short_url:edit', args=[link.pk])))

    assert 'type="datetime-local"' not in form
    assert re.search(r'data-utc-pair data-utc-target="id_expires_at_utc"', form)
    assert 'id="id_expires_date"' in form and 'id="id_expires_time"' in form
    # The hidden value is the UTC instant with an offset; the script splits it in the browser's zone.
    assert re.search(
        r'id="id_expires_at_utc" name="expires_at" type="hidden" value="2030-12-31T09:30Z"', form
    )
    # The two visible boxes have no name: only the UTC input is submitted.
    assert 'name="expires_date"' not in form and 'name="expires_time"' not in form


def test_the_day_and_week_bucket_labels_follow_the_format_and_months_stay_words():
    day = date(2026, 10, 25)

    with override(DisplayFormats('mdy', '12h')):
        assert stats._bucket_label(day, 1) == '10/25'
        assert stats._bucket_label(date(2025, 10, 25), 7) == '10/25/2025'
    with override(DisplayFormats('dmy', '24h')):
        assert stats._bucket_label(day, 1) == '25/10'
    assert stats._bucket_label(day, 1) == 'Oct 25'  # the public wording, as the API reports it
    for formats in (DisplayFormats('mdy', '12h'), DisplayFormats('dmy', '24h')):
        with override(formats):
            assert stats._bucket_label(date(2026, 10, 1), 0) == 'Oct 2026'
