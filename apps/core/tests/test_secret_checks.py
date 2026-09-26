import pytest

from config.secret_checks import insecure_secrets


@pytest.mark.parametrize(
    'value',
    [
        None,
        '',
        'django-insecure-8eho-(3@jki^spuj0q%+k!m9a',
        'django-insecure-change-in-production',
        'change-me-in-production',
        '<JWT_SIGNING_SECRET>',
    ],
)
def test_flags_missing_and_placeholder_values(value):
    assert insecure_secrets({'SECRET_KEY': value}) == ['SECRET_KEY']


def test_accepts_real_values():
    secrets = {'SECRET_KEY': 'k3y-7b1c9f0a2e', 'JWT_SECRET': 'aF93kd0-ss1'}

    assert insecure_secrets(secrets) == []
