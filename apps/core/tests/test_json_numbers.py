import json

import pytest

from apps.core.json_numbers import whole_number

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        (0, 0),
        (10, 10),
        (-3, -3),
        (1000.0, 1000),
        (1e3, 1000),
        ('42', 42),
        ('-7', -7),
        (10**400, 10**400),
    ],
)
def test_whole_numbers_are_accepted(raw, expected):
    assert whole_number(raw) == expected


@pytest.mark.parametrize(
    'raw',
    [
        json.loads('1e400'),  # infinity
        json.loads('-1e400'),
        float('nan'),
        1.5,
        '1.5',
        '',
        'abc',
        True,
        False,
        None,
        [1],
        {'a': 1},
    ],
)
def test_anything_else_is_none_and_never_raises(raw):
    assert whole_number(raw) is None
