import pytest

from ..forms import FeedbackForm
from ..models import MAX_DESCRIPTION_LENGTH

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize('value', ['', '   ', '\n\r\n\t'])
def test_blank_is_rejected(value):
    form = FeedbackForm({'description': value})

    assert not form.is_valid()
    assert form.errors['description'] == ['Please write your feedback.']


def test_too_long_is_rejected():
    form = FeedbackForm({'description': 'x' * (MAX_DESCRIPTION_LENGTH + 1)})

    assert not form.is_valid()
    assert 'at most' not in form.errors['description'][0]
    assert '5,000' in form.errors['description'][0]


def test_exactly_the_limit_is_accepted():
    assert FeedbackForm({'description': 'x' * MAX_DESCRIPTION_LENGTH}).is_valid()


def test_input_is_stripped_and_line_endings_normalized():
    form = FeedbackForm({'description': '  first\r\nsecond \n'})

    assert form.is_valid()
    assert form.cleaned_data['description'] == 'first\nsecond'


def test_crlf_does_not_count_twice_against_the_limit():
    text = 'x\r\n' * 2499 + 'xy'  # 5,000 characters once normalized, 7,499 raw

    assert FeedbackForm({'description': text}).is_valid()
