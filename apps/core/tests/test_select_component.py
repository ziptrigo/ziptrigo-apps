"""The site's themed select (`core/components/select.html`): what the include renders."""

import re

import pytest
from django.template.loader import render_to_string

pytestmark = pytest.mark.unit

OPTIONS = [
    {'value': 'a', 'label': 'Alpha', 'hint': 'first'},
    {'value': 'b', 'label': 'Beta'},
    {'value': 'c', 'label': 'Gamma <b>'},
]


def _render(**context) -> str:
    context = {'name': 'letter', 'id': 'letter', 'options': OPTIONS, 'value': 'b', **context}
    return render_to_string('core/components/select.html', context)


def test_submits_one_hidden_input_with_the_selected_value():
    html = _render()

    inputs = re.findall(r'<input type="hidden"[^>]*>', html)
    assert len(inputs) == 1
    assert 'name="letter"' in inputs[0] and 'value="b"' in inputs[0]
    assert html.count('name="letter"') == 2  # the hidden input and the no-JavaScript select


def test_the_button_is_a_labelable_combobox_for_a_listbox():
    html = _render()

    button = re.search(r'<button[^>]*>', html, flags=re.DOTALL)
    assert button is not None
    assert 'id="letter"' in button.group(0) and 'role="combobox"' in button.group(0)
    assert 'aria-haspopup="listbox"' in button.group(0)
    assert 'role="listbox"' in html
    assert html.count('role="option"') == len(OPTIONS)


def test_shows_the_selected_option_and_hints():
    html = re.sub(r'\s+', ' ', _render(value='a'))

    assert '<span x-text="label">Alpha</span>' in html
    assert '(first)' in html
    # An unknown value shows nothing until the script falls back to the first option.
    assert '<span x-text="label">' not in re.sub(r'\s+', ' ', _render(value='zzz'))


def test_escapes_labels_and_values():
    html = _render()

    assert 'Gamma &lt;b&gt;' in html
    assert 'Gamma <b>' not in html


def test_without_javascript_a_native_select_takes_over():
    html = _render()

    noscript = re.search(r'<noscript>.*?</noscript>', html, flags=re.DOTALL)
    assert noscript is not None
    assert '<select name="letter"' in noscript.group(0)
    assert re.search(r'<option value="b" selected>', noscript.group(0))


def test_disabled_and_root_class():
    html = _render(disabled=True, root_class='max-w-xs')

    assert 'max-w-xs' in html
    assert len(re.findall(r'<(?:input|button)[^>]* disabled', html)) == 2
