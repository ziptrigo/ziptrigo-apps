"""The shared pill (`core_ui`, issue #214) and the empty-state partial."""

import re

import pytest
from django.template import Context, Template
from django.template.loader import render_to_string

from apps.core.templatetags.core_ui import BASE, TONES, pill_classes, pill_html

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize('tone', sorted(TONES))
def test_every_tone_has_a_light_and_a_dark_colour(tone):
    classes = TONES[tone]
    assert re.search(r'(?<![:\w])bg-', classes), 'no light fill'
    assert re.search(r'(?<![:\w])text-', classes), 'no light text'
    assert 'dark:bg-' in classes
    assert 'dark:text-' in classes


@pytest.mark.parametrize('tone', ['green', 'amber', 'blue', 'red', 'sky'])
def test_coloured_tones_use_the_contrast_checked_shades(tone):
    """-700 text on a -50 fill in light mode (4.5:1 or better); -300 on a 10% tint in dark mode."""
    classes = TONES[tone]
    assert f'bg-{tone}-50' in classes
    assert f'text-{tone}-700' in classes
    assert f'dark:bg-{tone}-400/10' in classes
    assert f'dark:text-{tone}-300' in classes


def test_pill_shape_is_the_shared_one():
    html = str(pill_html('green', 'Processed'))
    for token in (
        'inline-flex',
        'rounded-full',
        'px-2',
        'py-0.5',
        'text-xs',
        'font-medium',
        'whitespace-nowrap',
    ):
        assert token in html
    assert BASE in html


def test_pill_with_icon_title_and_screen_reader_text():
    html = str(
        pill_html(
            'red', 'Today', icon='far fa-calendar', title='Due 2026-10-31', sr_only=' (overdue)'
        )
    )
    assert 'far fa-calendar text-[10px]' in html
    assert 'aria-hidden="true"' in html
    assert 'title="Due 2026-10-31"' in html
    assert '<span class="sr-only"> (overdue)</span>' in html
    assert 'gap-1.5' in html


def test_pill_without_an_icon_has_no_gap_or_icon_element():
    html = str(pill_html('gray', 'Promo'))
    assert '<i ' not in html
    assert 'gap-1.5' not in html


def test_pill_escapes_its_text_and_title():
    html = str(pill_html('gray', '<b>x</b>', title='"><script>'))
    assert '<b>x</b>' not in html
    assert '&lt;b&gt;' in html
    assert '<script>' not in html


def test_pill_classes_for_markup_a_template_writes_itself():
    assert pill_classes('amber', 'tabular-nums').endswith('tabular-nums')
    assert TONES['amber'] in pill_classes('amber')


def test_pill_tags_render_from_a_template():
    out = Template(
        "{% load core_ui %}{% pill 'green' 'Done' icon='fas fa-check' %}|{% pill_class 'amber' %}"
    ).render(Context())
    assert 'Done' in out
    assert 'fas fa-check' in out
    assert TONES['amber'] in out


class TestEmptyState:
    def render(self, **context):
        return render_to_string('core/partials/empty_state.html', context)

    def test_title_icon_text_and_action(self):
        html = self.render(
            icon='fas fa-receipt',
            title='No receipts yet',
            text='Upload one.',
            action_url='/receipts/upload/',
            action_label='Upload receipts',
        )
        assert 'No receipts yet' in html
        assert 'fas fa-receipt' in html
        assert 'Upload one.' in html
        assert 'href="/receipts/upload/"' in html
        assert 'Upload receipts' in html

    def test_a_search_with_no_match_has_no_button(self):
        html = self.render(
            icon='fas fa-magnifying-glass', title='No receipts found', text='Try other filters.'
        )
        assert 'No receipts found' in html
        assert '<a ' not in html

    def test_it_is_escaped(self):
        html = self.render(title='<script>alert(1)</script>')
        assert '<script>' not in html
