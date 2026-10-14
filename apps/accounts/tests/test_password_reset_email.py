"""Unit tests for password reset email rendering using a Django template."""

from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.services.password_reset import render_password_reset_email

User = get_user_model()


def test_render_password_reset_email_includes_user_name_and_url(settings, client, user):
    """Rendered email should contain user name (or email) and reset URL in both bodies."""

    # Build a fake reset URL similar to real one.
    reset_url = 'https://example.com' + reverse('accounts:reset-password', args=['TOKEN'])

    subject, text_body, html_body = render_password_reset_email(user=user, reset_url=reset_url)

    assert 'Reset your ZipTrigo account password' in subject
    # Text body should mention the user's name and URL.
    assert user.name in text_body or user.email in text_body
    assert reset_url in text_body
    # HTML body should mention the user's name and URL.
    assert user.name in html_body or user.email in html_body
    assert reset_url in html_body


def test_render_password_reset_email_works_without_name(settings, client, db):
    """If user has no name, email should be used in greeting and template still renders."""

    UserModel = get_user_model()
    user = UserModel.objects.create_user(
        email='noname@example.com',
        password='password123',
        name='',
    )

    reset_url = 'https://example.com' + reverse('accounts:reset-password', args=['TOKEN2'])

    subject, text_body, html_body = render_password_reset_email(user=user, reset_url=reset_url)

    assert 'Reset your ZipTrigo account password' in subject
    assert 'noname@example.com' in text_body
    assert reset_url in text_body
    assert 'noname@example.com' in html_body
    assert reset_url in html_body


def test_render_password_reset_email_escapes_the_name(db):
    """The name is free text -- for an account its creator never confirmed, whatever they typed --
    so it must not reach the HTML body as markup."""
    user = User(
        email='victim@example.com', name='<a href="https://evil.example">x</a><script>1</script>'
    )

    _, text_body, html_body = render_password_reset_email(
        user=user, reset_url='https://example.com/reset-password/T/'
    )

    assert '<script>' not in html_body
    assert '<a href="https://evil.example"' not in html_body
    assert '&lt;script&gt;' in html_body
    # The plain-text body is text, so it is left as typed.
    assert '<script>' in text_body
