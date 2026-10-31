"""Per-field validation errors for the 422 responses (issue #219): the helpers that collect them,
the `{% field_errors %}` tag that hands them to the page, and that the script is wired in."""

import pytest
from django import forms
from django.core.exceptions import ValidationError
from django.template import Context, Template
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy

from apps.core.field_errors import (
    ErrorLines,
    field_errors_of,
    form_lines,
    from_form,
    message_lines,
    validation_lines,
)

pytestmark = [pytest.mark.unit]


class SampleForm(forms.Form):
    name = forms.CharField(label='Your name', max_length=5)
    email = forms.EmailField(label='Email')

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('name') == 'abc':
            raise forms.ValidationError('That name is reserved.')
        return cleaned


def _bad_form(**data) -> SampleForm:
    form = SampleForm({'name': 'too long a name', 'email': 'nope', **data})
    assert not form.is_valid()
    return form


def _render(source, **kwargs) -> str:
    template = Template(
        '{% load field_errors %}{% field_errors source ' + kwargs.pop('args', '') + ' %}'
    )
    return template.render(Context({'source': source}))


class TestFromForm:
    def test_it_keys_each_message_by_the_name_of_its_input(self):
        errors = from_form(_bad_form())

        assert list(errors) == ['name', 'email']
        assert 'at most 5' in errors['name'][0]
        assert errors['email'] == ['Enter a valid email address.']

    def test_a_prefixed_form_is_found_under_its_prefixed_name(self):
        form = SampleForm({'p-name': 'too long a name', 'p-email': 'ok@example.com'}, prefix='p')
        assert not form.is_valid()

        assert list(from_form(form)) == ['p-name']

    def test_an_error_that_belongs_to_no_field_is_left_out(self):
        form = SampleForm({'name': 'abc', 'email': 'ok@example.com'})
        assert not form.is_valid()

        assert form.non_field_errors() == ['That name is reserved.']
        assert from_form(form) == {}


class TestFormLines:
    def test_the_lines_are_the_slots_non_field_first_then_label_and_message(self):
        form = _bad_form()
        form.add_error(None, 'Something general.')

        lines = form_lines(form)

        assert lines[0] == 'Something general.'
        assert lines[1].startswith('Your name: ')
        assert lines[2] == 'Email: Enter a valid email address.'
        assert isinstance(lines, list)

    def test_they_carry_the_fields_messages_but_not_the_non_field_ones(self):
        form = _bad_form()
        form.add_error(None, 'Something general.')

        errors = form_lines(form).field_errors

        assert set(errors) == {'name', 'email'}
        assert not any('Something general.' in messages for messages in errors.values())


class TestValidationLines:
    def test_a_field_keyed_error_gets_its_label_in_the_line_and_its_message_in_the_field_data(self):
        exc = ValidationError({'expires_at': ['Must be in the future.'], '__all__': ['Nope.']})

        lines = validation_lines(exc, {'expires_at': 'Expires'})

        assert list(lines) == ['Nope.', 'Expires: Must be in the future.']
        assert lines.field_errors == {'expires_at': ['Must be in the future.']}

    def test_an_unlabelled_field_is_named_from_its_key(self):
        lines = validation_lines(ValidationError({'click_limit': ['Too many.']}))

        assert list(lines) == ['Click limit: Too many.']

    def test_names_maps_a_service_field_to_the_input_that_has_it(self):
        lines = validation_lines(
            ValidationError({'password': ['Too short.']}), names={'password': 'new_password'}
        )

        assert lines.field_errors == {'new_password': ['Too short.']}

    def test_a_non_field_error_comes_first_whatever_the_order_the_exception_lists_it(self):
        exc = ValidationError({'alias': ['Taken.'], '__all__': ['Nope.'], 'title': ['Too long.']})

        lines = validation_lines(exc, {'alias': 'Alias', 'title': 'Title'})

        assert list(lines) == ['Nope.', 'Alias: Taken.', 'Title: Too long.']
        assert lines.field_errors == {'alias': ['Taken.'], 'title': ['Too long.']}

    def test_two_service_fields_for_one_input_keep_both_messages(self):
        exc = ValidationError({'utm_source': ['Bad.'], 'utm_medium': ['Worse.']})

        lines = validation_lines(exc, names={'utm_source': 'utm', 'utm_medium': 'utm'})

        assert lines.field_errors == {'utm': ['Bad.', 'Worse.']}

    def test_an_error_with_no_field_has_no_field_data(self):
        lines = validation_lines(ValidationError('Not allowed.'))

        assert list(lines) == ['Not allowed.']
        assert lines.field_errors == {}


class TestMessageLines:
    def test_the_lines_stay_bare_but_the_fields_are_kept(self):
        lines = message_lines(ValidationError({'name': ['Taken.'], '__all__': ['Also this.']}))

        assert list(lines) == ['Taken.', 'Also this.']
        assert lines.field_errors == {'name': ['Taken.']}


class TestFieldErrorsOf:
    def test_a_bound_form(self):
        assert set(field_errors_of(_bad_form())) == {'name', 'email'}

    def test_an_unbound_form_has_none(self):
        assert field_errors_of(SampleForm()) == {}

    def test_error_lines(self):
        assert field_errors_of(ErrorLines(['x'], {'a': ['b']})) == {'a': ['b']}

    def test_a_dict_whose_value_is_a_single_message(self):
        assert field_errors_of({'a': 'one', 'b': ['two', 'three']}) == {
            'a': ['one'],
            'b': ['two', 'three'],
        }

    def test_a_lazy_translation_is_one_message_not_a_string_of_characters(self):
        errors = field_errors_of({'a': gettext_lazy('Fix this.'), 'b': [gettext_lazy('And this.')]})

        assert errors == {'a': ['Fix this.'], 'b': ['And this.']}
        assert all(type(m) is str for messages in errors.values() for m in messages)

    def test_validation_errors_and_an_error_list_are_read_as_their_messages(self):
        form = _bad_form()
        errors = field_errors_of(
            {
                'a': ValidationError('One.'),
                'b': [ValidationError('Two.'), 'Three.'],
                'c': form.errors['email'],  # an ErrorList
            }
        )

        assert errors == {
            'a': ['One.'],
            'b': ['Two.', 'Three.'],
            'c': ['Enter a valid email address.'],
        }

    @pytest.mark.parametrize('source', [None, '', 'a message', ['a', 'b']])
    def test_anything_else_has_none(self, source):
        assert field_errors_of(source) == {}


class TestTheTag:
    def test_one_hidden_item_per_message_named_by_its_input(self):
        html = _render({'expires_at': ['Must be in the future.', 'Or empty.'], 'alias': ['Taken.']})

        assert html.count('<li data-field-error=') == 3
        assert '<ul hidden data-field-errors>' in html
        assert '<li data-field-error="expires_at">Must be in the future.</li>' in html
        assert '<li data-field-error="alias">Taken.</li>' in html

    def test_nothing_is_rendered_without_errors(self):
        assert _render({}).strip() == ''
        assert _render(SampleForm()).strip() == ''
        assert _render(None).strip() == ''

    def test_the_messages_are_escaped(self):
        html = _render({'name': ['<script>alert(1)</script>']})

        assert '<script>' not in html
        assert '&lt;script&gt;' in html

    def test_a_scope_names_the_element_to_look_in(self):
        html = _render({'name': ['x']}, args='scope="row-1"')

        assert 'data-scope="row-1"' in html

    def test_without_a_fallback_there_is_no_noscript(self):
        assert '<noscript>' not in _render({'name': ['x']})

    def test_a_fallback_lists_the_messages_for_a_visitor_without_javascript(self):
        html = _render({'name': ['Fix this.']}, args='fallback=True')

        assert '<noscript>' in html
        assert '<li>Fix this.</li>' in html

    def test_the_noscript_list_is_escaped_and_does_not_hold_the_hidden_data(self):
        html = _render({'name': ['<b>Fix</b> this.']}, args='fallback=True')

        noscript = html.split('<noscript>')[1].split('</noscript>')[0]
        assert '&lt;b&gt;Fix&lt;/b&gt; this.' in noscript
        assert '<b>' not in html
        assert 'data-field-error' not in noscript


def test_the_script_is_loaded_by_every_page():
    html = render_to_string('core/base.html', {'products': []})

    assert 'core/js/field_errors.js' in html


def test_the_date_picker_marks_its_wrapper_so_a_click_inside_it_counts_as_editing():
    """`field_errors.js` clears a picker's message on a click inside `[data-field-wrap]`, not inside
    any `.relative` box."""
    html = render_to_string('core/components/date_picker.html', {'name': 'expiry_date'})

    assert 'data-field-wrap' in html
    assert html.index('data-field-wrap') < html.index('<input type="hidden" name="expiry_date"')
