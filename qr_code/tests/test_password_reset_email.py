"""Unit tests for password reset email rendering using Jinja2 template."""

from django.contrib.auth import get_user_model
from django.urls import reverse
from src.qr_code.services.password_reset import render_password_reset_email

User = get_user_model()


def test_render_password_reset_email_includes_user_name_and_url(settings, client, user):
    """Rendered email should contain user name (or email) and reset URL in both bodies."""

    # Build a fake reset URL similar to real one.
    reset_url = 'https://example.com' + reverse('reset-password-page', args=['TOKEN'])

    subject, text_body, html_body = render_password_reset_email(user=user, reset_url=reset_url)

    assert 'Reset your QR Code account password' in subject
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
        username='noname',
        email='noname@example.com',
        password='password123',
        name='',
    )

    reset_url = 'https://example.com' + reverse('reset-password-page', args=['TOKEN2'])

    subject, text_body, html_body = render_password_reset_email(user=user, reset_url=reset_url)

    assert 'Reset your QR Code account password' in subject
    assert 'noname@example.com' in text_body
    assert reset_url in text_body
    assert 'noname@example.com' in html_body
    assert reset_url in html_body
