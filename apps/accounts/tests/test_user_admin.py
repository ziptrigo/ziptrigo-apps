from uuid import UUID

import pytest
from django.urls import reverse

from apps.accounts.models import User

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_add_user_hashes_password(client, admin_user):
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:accounts_user_add'),
        {
            'email': 'new@example.com',
            'name': 'New User',
            'usable_password': 'true',
            'password1': 'S3cure-pass-42',
            'password2': 'S3cure-pass-42',
        },
    )

    assert response.status_code == 302
    user = User.objects.get(email='new@example.com')
    assert user.password != 'S3cure-pass-42'
    assert user.check_password('S3cure-pass-42')


def test_change_form_shows_password_hash_read_only(client, admin_user, regular_user):
    client.force_login(admin_user)

    response = client.get(reverse('custom_admin:accounts_user_change', args=[regular_user.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="password"' not in content
    # The widget links to the change-password form, relative to the change page.
    assert 'href="../password/"' in content
    password_url = reverse('custom_admin:auth_user_password_change', args=[regular_user.pk])
    assert client.get(password_url).status_code == 200


def test_change_form_saves_without_touching_password(client, admin_user, regular_user):
    client.force_login(admin_user)
    old_hash = regular_user.password

    response = client.post(
        reverse('custom_admin:accounts_user_change', args=[regular_user.pk]),
        {
            'email': regular_user.email,
            'name': 'Renamed',
            'status': User.STATUS_ACTIVE,
            'is_active': 'on',
        },
    )

    assert response.status_code == 302
    regular_user.refresh_from_db()
    assert regular_user.name == 'Renamed'
    assert regular_user.password == old_hash


# -- changing the email address in the admin --


@pytest.fixture
def sent_confirmations(monkeypatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(
        'apps.accounts.services.email_confirmation.EmailConfirmationService.send_confirmation_email',
        lambda self, user: sent.append(user.email),
    )
    return sent


@pytest.fixture
def sent_notices(monkeypatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(
        'apps.accounts.services.email_change.send_email', lambda **kwargs: sent.append(kwargs['to'])
    )
    return sent


def _change(client, user, **fields):
    data = {
        'email': user.email,
        'name': user.name,
        'status': User.STATUS_ACTIVE,
        'is_active': 'on',
    }
    if user.email_confirmed:
        data['email_confirmed'] = 'on'
    data.update(fields)
    return client.post(reverse('custom_admin:accounts_user_change', args=[user.pk]), data)


def _pending_for(email: str) -> UUID:
    from apps.core.services.email_verification import EmailVerificationContext, start

    def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
        return 'subject', 'text', 'html'

    return start(email, 'accounts.email_confirmation', build_email=build_email)


def test_changing_the_email_unconfirms_and_retires_the_old_links(
    client,
    admin_user,
    user,
    sent_confirmations,
    sent_notices,
    django_capture_on_commit_callbacks,
):
    from apps.core.models import EmailVerification

    client.force_login(admin_user)
    pending_id = _pending_for(user.email)

    with django_capture_on_commit_callbacks(execute=True):
        response = _change(client, user, email='moved@example.com')

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.email == 'moved@example.com'
    assert not user.email_confirmed
    assert user.email_confirmed_at is None
    assert EmailVerification.objects.get(pk=pending_id).invalidated_at is not None
    assert sent_confirmations == ['moved@example.com']
    assert sent_notices == ['testuser@example.com']


def test_ticking_confirmed_with_the_new_email_keeps_it_confirmed(
    client, admin_user, regular_user, sent_confirmations, sent_notices
):
    from apps.core.models import EmailVerification

    client.force_login(admin_user)
    old_email = regular_user.email
    pending_id = _pending_for(old_email)

    response = _change(client, regular_user, email='moved@example.com', email_confirmed='on')

    assert response.status_code == 302
    regular_user.refresh_from_db()
    assert regular_user.email == 'moved@example.com'
    assert regular_user.email_confirmed
    assert EmailVerification.objects.get(pk=pending_id).invalidated_at is not None
    assert sent_confirmations == []
    # The old address was never confirmed, so it is not told.
    assert sent_notices == []


def test_saving_without_changing_the_email_leaves_confirmation_alone(
    client, admin_user, user, sent_confirmations
):
    client.force_login(admin_user)

    response = _change(client, user, name='Renamed')

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.email_confirmed
    assert sent_confirmations == []
