"""`{% local_time %}`: the UTC fallback the server renders, and the attributes the script reads."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from django.template import Context, Template, TemplateSyntaxError
from django.utils import timezone as dj_timezone

from apps.core import datetime_format as df

MOMENT = datetime(2026, 10, 27, 13, 51, 30, tzinfo=UTC)
ATTRS = 'data-date-format="{}" data-time-format="{}"'


def render(source: str, **context) -> str:
    return Template('{% load local_time %}' + source).render(Context(context))


@pytest.fixture(autouse=True)
def _now(monkeypatch):
    """Fix "now" so the current-year rule of the short style is deterministic."""
    monkeypatch.setattr(dj_timezone, 'now', lambda: datetime(2026, 6, 1, 12, 0, tzinfo=UTC))


def test_long_is_the_default_and_public_off_a_signed_in_page():
    assert render('{% local_time t %}', t=MOMENT) == (
        f'<time datetime="2026-10-27T13:51:30+00:00" data-local-time="long" {ATTRS.format("public", "12h")}>'
        'Oct 27, 2026, 1:51 PM UTC</time>'
    )


@pytest.mark.parametrize(
    ('formats', 'style', 'text'),
    [
        (df.PUBLIC, 'long', 'Oct 27, 2026, 1:51 PM UTC'),
        (df.PUBLIC, 'short', 'Oct 27, 1:51 PM UTC'),
        (df.PUBLIC, 'iso', '2026-10-27 1:51 PM UTC'),
        (df.DisplayFormats('mdy', '12h'), 'long', '10/27/2026, 1:51 PM UTC'),
        (df.DisplayFormats('mdy', '12h'), 'short', '10/27, 1:51 PM UTC'),
        (df.DisplayFormats('dmy', '24h'), 'long', '27/10/2026, 13:51 UTC'),
        (df.DisplayFormats('dmy', '24h'), 'short', '27/10, 13:51 UTC'),
        (df.DisplayFormats('dmy', '24h'), 'iso', '2026-10-27 13:51 UTC'),
    ],
)
def test_the_fallback_follows_the_active_formats(formats, style, text):
    with df.override(formats):
        html = render('{% local_time t style %}', t=MOMENT, style=style)

    assert f'data-local-time="{style}"' in html
    assert ATTRS.format(formats.date_order, formats.clock) in html
    assert html.endswith(f'>{text}</time>')


def test_short_adds_the_year_when_it_is_not_this_year():
    with df.override(df.DisplayFormats('mdy', '12h')):
        html = render('{% local_time t "short" %}', t=MOMENT.replace(year=2025))

    assert html.endswith('>10/27/2025, 1:51 PM UTC</time>')


def test_another_offset_is_shown_in_utc():
    chicago = MOMENT.astimezone(timezone(timedelta(hours=-5)))
    html = render('{% local_time t %}', t=chicago)
    assert 'datetime="2026-10-27T13:51:30+00:00"' in html
    assert 'Oct 27, 2026, 1:51 PM UTC' in html


def test_the_fallback_ignores_the_active_time_zone():
    from zoneinfo import ZoneInfo

    try:
        dj_timezone.activate(ZoneInfo('Asia/Tokyo'))
        html = render('{% local_time t %}', t=MOMENT)
    finally:
        dj_timezone.deactivate()

    assert 'Oct 27, 2026, 1:51 PM UTC' in html


def test_a_naive_value_is_taken_as_utc():
    html = render('{% local_time t %}', t=MOMENT.replace(tzinfo=None))
    assert 'datetime="2026-10-27T13:51:30+00:00"' in html


def test_none_renders_nothing():
    assert render('{% local_time t %}', t=None) == ''


def test_title_only_renders_attributes_for_an_element_of_ones_own():
    with df.override(df.DisplayFormats('dmy', '24h')):
        html = render("<time {% local_time t 'iso' title=True %}>3 hours ago</time>", t=MOMENT)
    assert html == (
        '<time datetime="2026-10-27T13:51:30+00:00" data-local-time="iso" '
        'data-date-format="dmy" data-time-format="24h" '
        'data-local-time-title title="2026-10-27 13:51 UTC">3 hours ago</time>'
    )


def test_an_unknown_style_is_an_error():
    with pytest.raises(TemplateSyntaxError):
        render("{% local_time t 'weird' %}", t=MOMENT)


def test_every_page_loads_the_scripts(client):
    content = client.get('/').content.decode()
    assert 'core/js/local_time.js' in content
    assert 'core/js/datetime_format.js' in content
    assert content.index('core/js/datetime_format.js') < content.index('core/js/local_time.js')
