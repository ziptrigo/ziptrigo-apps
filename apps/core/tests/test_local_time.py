"""`{% local_time %}`: the UTC fallback the server renders, and the attributes the script reads."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from django.template import Context, Template, TemplateSyntaxError

MOMENT = datetime(2026, 10, 27, 13, 51, 30, tzinfo=UTC)


def render(source: str, **context) -> str:
    return Template('{% load local_time %}' + source).render(Context(context))


def test_long_is_the_default():
    assert render('{% local_time t %}', t=MOMENT) == (
        '<time datetime="2026-10-27T13:51:30+00:00" data-local-time="long">'
        'Oct 27, 2026 13:51 UTC</time>'
    )


@pytest.mark.parametrize(
    ('style', 'text'), [('short', 'Oct 27, 13:51 UTC'), ('iso', '2026-10-27 13:51 UTC')]
)
def test_styles(style, text):
    html = render('{% local_time t style %}', t=MOMENT, style=style)
    assert f'data-local-time="{style}">{text}</time>' in html


def test_another_offset_is_shown_in_utc():
    chicago = MOMENT.astimezone(timezone(timedelta(hours=-5)))
    html = render('{% local_time t %}', t=chicago)
    assert 'datetime="2026-10-27T13:51:30+00:00"' in html
    assert 'Oct 27, 2026 13:51 UTC' in html


def test_a_naive_value_is_taken_as_utc():
    html = render('{% local_time t %}', t=MOMENT.replace(tzinfo=None))
    assert 'datetime="2026-10-27T13:51:30+00:00"' in html


def test_none_renders_nothing():
    assert render('{% local_time t %}', t=None) == ''


def test_title_only_renders_attributes_for_an_element_of_ones_own():
    html = render("<time {% local_time t 'iso' title=True %}>3 hours ago</time>", t=MOMENT)
    assert html == (
        '<time datetime="2026-10-27T13:51:30+00:00" data-local-time="iso" '
        'data-local-time-title title="2026-10-27 13:51 UTC">3 hours ago</time>'
    )


def test_an_unknown_style_is_an_error():
    with pytest.raises(TemplateSyntaxError):
        render("{% local_time t 'weird' %}", t=MOMENT)


def test_every_page_loads_the_script(client):
    assert 'core/js/local_time.js' in client.get('/').content.decode()
