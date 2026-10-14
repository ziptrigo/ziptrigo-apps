"""The confirmation email's rendering. Its greeting carries the account's free-text name, which
for an account that was never confirmed is whatever its creator typed -- and the address's owner
may be the one reading it (issue #125 review)."""

from apps.accounts.models import User
from apps.accounts.services.email_confirmation import render_email_confirmation_email


def test_name_is_escaped_in_the_html_body():
    user = User(
        email='victim@example.com',
        name='<a href="https://evil.example">Click</a><script>alert(1)</script>',
    )

    subject, text_body, html_body = render_email_confirmation_email(
        user=user,
        confirmation_url='https://example.com/account/confirm-email/T/',
        validity_minutes=60,
    )

    assert subject == 'Confirm your ZipTrigo account email'
    assert '<script>' not in html_body
    assert '<a href="https://evil.example"' not in html_body
    assert '&lt;script&gt;' in html_body
    assert '&lt;a href=' in html_body
    # The link itself is intact, and the plain-text body is text.
    assert 'href="https://example.com/account/confirm-email/T/"' in html_body
    assert 'https://example.com/account/confirm-email/T/' in text_body


def test_greeting_falls_back_to_the_address_without_a_name():
    user = User(email='noname@example.com', name='')

    _, text_body, html_body = render_email_confirmation_email(
        user=user, confirmation_url='https://example.com/c/', validity_minutes=2880
    )

    assert 'Hi noname@example.com,' in text_body
    assert 'Hi noname@example.com,' in html_body
    assert '48 hours' in html_body


def test_copy_tells_the_reader_to_press_the_button_and_fits_signup_and_email_change():
    user = User(email='a@example.com', name='A')

    _, text_body, html_body = render_email_confirmation_email(
        user=user, confirmation_url='https://example.com/c/', validity_minutes=60
    )

    assert 'press "Confirm my email"' in text_body
    assert 'press <strong>Confirm my email</strong>' in html_body
    # Also sent after an email change, so it must not claim a new account was created.
    assert 'creating an account' not in text_body + html_body
