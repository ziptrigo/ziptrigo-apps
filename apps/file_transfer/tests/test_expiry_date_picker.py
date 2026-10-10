from datetime import date

from django.utils import timezone

from apps.core.forms import DatePickerWidget
from apps.file_transfer.forms import SendOptionsForm


def test_widget_renders_hidden_value_and_min():
    html = DatePickerWidget(min_today=True).render('d', date(2030, 1, 2))
    assert 'name="d"' in html
    assert 'data-value="2030-01-02"' in html
    assert f'data-min="{timezone.localdate().isoformat()}"' in html


def test_send_form_turns_picked_date_into_end_of_that_day():
    form = SendOptionsForm(
        data={'recipients': 'a@example.com', 'expiry_choice': 'custom', 'expiry_date': '2099-03-04'}
    )
    assert form.is_valid(), form.errors
    expires = timezone.localtime(form.cleaned_data['expiry_date'])
    assert (expires.year, expires.month, expires.day, expires.hour, expires.minute) == (
        2099,
        3,
        4,
        23,
        59,
    )


def test_send_form_requires_a_date_for_custom_expiry():
    form = SendOptionsForm(data={'recipients': 'a@example.com', 'expiry_choice': 'custom'})
    assert not form.is_valid()
    assert 'expiry_date' in form.errors
