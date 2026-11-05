"""The site's date field and time field (issue #245): what the widgets and the includes render."""

import re
from datetime import date, datetime, time
from html import unescape

import pytest
from django import forms
from django.template.loader import render_to_string

from apps.core import datetime_format as df
from apps.core.forms import DatePickerWidget, TimeFieldWidget


def _tag(html: str, pattern: str) -> str:
    match = re.search(pattern, html, flags=re.DOTALL)
    assert match is not None, pattern
    return match.group(0)


def _hidden(html: str) -> str:
    return _tag(html, r'<input type="hidden"[^>]*>')


def _text(html: str) -> str:
    return _tag(html, r'<input type="text"[^>]*>')


# --- the date field ---


def test_date_widget_submits_the_iso_value_in_a_hidden_input():
    html = DatePickerWidget().render('expiry_date', date(2026, 11, 5), {'id': 'id_expiry'})

    assert 'name="expiry_date"' in _hidden(html)
    assert 'value="2026-11-05"' in _hidden(html)
    # The visible box has no name, so it is never submitted; the label points at it.
    assert 'name=' not in _text(html)
    assert 'id="id_expiry"' in _text(html)


@pytest.mark.parametrize(
    ('formats', 'text', 'placeholder'),
    [
        (df.DisplayFormats('mdy', '12h'), '11/05/2026', 'mm/dd/yyyy'),
        (df.DisplayFormats('dmy', '24h'), '05/11/2026', 'dd/mm/yyyy'),
        (df.PUBLIC, '11/05/2026', 'mm/dd/yyyy'),
    ],
)
def test_date_widget_shows_the_date_in_the_users_order(formats, text, placeholder):
    with df.override(formats):
        html = DatePickerWidget().render('d', date(2026, 11, 5))

    box = _text(html)
    assert f'value="{text}"' in box
    assert f'placeholder="{placeholder}"' in box


def test_date_widget_without_a_value():
    html = DatePickerWidget().render('d', None)

    assert 'value=""' in _hidden(html)
    assert 'value=""' in _text(html)


def test_date_widget_min_max_and_flags():
    html = DatePickerWidget(attrs={'min': '2026-01-01', 'max': '2026-12-31'}).render(
        'd', '2026-06-01', {'aria-label': 'Expiry', 'disabled': True}
    )

    assert 'data-min="2026-01-01"' in html
    assert 'data-max="2026-12-31"' in html
    assert 'aria-label="Expiry"' in _text(html)
    assert 'disabled' in _hidden(html) and 'disabled' in _text(html)


def test_date_widget_min_today(settings):
    from django.utils import timezone

    html = DatePickerWidget(min_today=True).render('d', None)

    assert f'data-min="{timezone.localdate().isoformat()}"' in html


def test_date_widget_takes_a_datetime():
    html = DatePickerWidget().render('d', datetime(2026, 11, 5, 23, 0))

    assert 'value="2026-11-05"' in _hidden(html)


def test_date_include_with_an_alpine_disabled_expression_and_x_model():
    html = render_to_string(
        'core/components/date_picker.html',
        {
            'name': 'date_from',
            'id': 'df',
            'disabled_expr': "range !== 'custom'",
            'x_model': 'until',
        },
    )

    # Autoescaped in the attribute; the browser reads it back as the expression.
    expression = ':disabled="range !== \'custom\'"'
    assert expression in unescape(_hidden(html))
    assert expression in unescape(_text(html))
    assert 'x-model="until"' in html and 'x-modelable="value"' in html
    assert 'data-field-wrap' in html


def test_date_include_without_a_name_has_a_nameless_hidden_input():
    html = render_to_string('core/components/date_picker.html', {'name': ''})

    assert 'name=' not in _hidden(html)


def test_date_widget_in_a_form():
    class F(forms.Form):
        when = forms.DateField(widget=DatePickerWidget(min_today=True))

    html = str(F()['when'])

    assert 'name="when"' in html


# --- the time field ---


@pytest.mark.parametrize(
    ('formats', 'text', 'placeholder'),
    [
        (df.DisplayFormats('mdy', '12h'), '10:00 PM', '9:00 PM'),
        (df.DisplayFormats('dmy', '24h'), '22:00', '21:00'),
        (df.PUBLIC, '10:00 PM', '9:00 PM'),
    ],
)
def test_time_widget_shows_the_time_on_the_users_clock(formats, text, placeholder):
    with df.override(formats):
        html = TimeFieldWidget().render('due_time', time(22, 0), {'id': 'due'})

    assert 'name="due_time"' in _hidden(html)
    assert 'value="22:00"' in _hidden(html)
    box = _text(html)
    assert f'value="{text}"' in box
    assert f'placeholder="{placeholder}"' in box
    assert 'id="due"' in box
    assert 'name=' not in box


def test_time_widget_is_a_combobox_with_a_listbox():
    html = TimeFieldWidget().render('t', time(9, 30))

    assert 'role="combobox"' in _text(html)
    assert 'aria-autocomplete="list"' in _text(html)
    assert 'role="listbox"' in html
    assert 'role="option"' in html


def test_time_widget_flags():
    html = TimeFieldWidget().render('t', '09:30:00', {'required': True, 'aria-label': 'Time'})

    assert 'value="09:30"' in _hidden(html)
    assert 'data-required="true"' in html
    assert 'aria-label="Time"' in _text(html)


def test_time_widget_without_a_value_is_not_required():
    html = TimeFieldWidget().render('t', None)

    assert 'value=""' in _hidden(html)
    assert 'data-required="false"' in html


def test_scripts_are_loaded_on_every_page(client):
    content = client.get('/').content.decode()

    assert 'core/js/date_picker.js' in content
    assert 'core/js/time_field.js' in content
