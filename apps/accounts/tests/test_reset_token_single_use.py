"""A password reset link works once (audit #7): it is bound to the password the account had when
it was sent (`tokens.PASSWORD_CLAIM`), so setting a password -- by that link, another one, or the
account page -- refuses every link issued before. Covers the reset page and the API, which share
`PasswordResetService.validate_token`."""

import re
from urllib.parse import urlsplit

import pytest
from django.urls import reverse
from ninja_jwt.settings import api_settings

from apps.accounts.models import User
from apps.accounts.services.existing_account import handle_signup_for_existing_email
from apps.accounts.services.password_change import change_password
from apps.accounts.services.password_reset import InvalidResetTokenError, PasswordResetService
from apps.accounts.tokens import PasswordResetToken

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def _passwords(value: str) -> dict[str, str]:
    return {'password': value, 'password_confirm': value}


def _token(user: User) -> str:
    return str(PasswordResetToken.for_user(user))


def _url(token: str) -> str:
    return reverse('accounts:reset-password', args=[token])


def _link_path(message: dict) -> str:
    match = re.search(r'https?://\S+/reset-password/[^/\s]+/', message['text_body'])
    assert match is not None, message['text_body']
    return urlsplit(match.group(0)).path


def _api_reset(api_client, token: str, password: str):
    return api_client.post('/auth/reset-password', json={'token': token, **_passwords(password)})


def test_link_works_once_on_the_page(client, user):
    link = _url(_token(user))

    first = client.post(link, _passwords('firstpass123'), **HTMX)
    again = client.post(link, _passwords('secondpass123'), **HTMX)

    assert first['HX-Redirect'] == reverse('accounts:login')
    assert again['HX-Redirect'] == link  # refused: bounced back to the expired-link page
    after = User.objects.get(pk=user.pk)
    assert after.check_password('firstpass123')
    assert not after.check_password('secondpass123')


def test_used_link_no_longer_shows_the_form(client, user):
    link = _url(_token(user))
    assert client.get(link).status_code == 200

    client.post(link, _passwords('firstpass123'), **HTMX)

    assert 'expired' in client.get(link).content.decode().lower()


def test_link_works_once_through_the_api(api_client, user):
    token = _token(user)

    first = _api_reset(api_client, token, 'firstpass123')
    again = _api_reset(api_client, token, 'secondpass123')

    assert first.status_code == 200
    assert again.status_code == 400
    after = User.objects.get(pk=user.pk)
    assert after.check_password('firstpass123')
    assert not after.check_password('secondpass123')


def test_link_used_on_the_page_is_dead_for_the_api(client, api_client, user):
    token = _token(user)

    client.post(_url(token), _passwords('firstpass123'), **HTMX)

    assert _api_reset(api_client, token, 'secondpass123').status_code == 400


def test_link_issued_before_an_account_page_password_change_is_refused(client, api_client, user):
    token = _token(user)

    change_password(user, 'changedpass123')

    assert PasswordResetService.validate_token(token) is None
    assert 'expired' in client.get(_url(token)).content.decode().lower()
    posted = client.post(_url(token), _passwords('hijackpass123'), **HTMX)
    assert posted['HX-Redirect'] == _url(token)
    assert _api_reset(api_client, token, 'hijackpass123').status_code == 400
    assert User.objects.get(pk=user.pk).check_password('changedpass123')


def test_using_one_link_kills_the_others_still_out(user):
    older, newer = _token(user), _token(user)
    assert PasswordResetService.validate_token(older) is not None

    resolved = PasswordResetService.validate_token(newer)
    assert resolved is not None
    PasswordResetService.reset_password(resolved, 'firstpass123', token=newer)

    assert PasswordResetService.validate_token(older) is None
    assert PasswordResetService.validate_token(newer) is None


def test_link_without_the_password_claim_is_refused(client, user):
    """A link emailed before the binding existed: refused, like login tokens without the claim."""
    legacy = PasswordResetToken()
    legacy[api_settings.USER_ID_CLAIM] = str(user.pk)
    legacy['email'] = user.email

    assert PasswordResetService.validate_token(str(legacy)) is None
    posted = client.post(_url(str(legacy)), _passwords('hijackpass123'), **HTMX)
    assert posted['HX-Redirect'] == _url(str(legacy))
    assert not User.objects.get(pk=user.pk).check_password('hijackpass123')


def test_same_link_validated_twice_then_reset_twice_only_the_first_wins(user):
    """The race: two requests both pass `validate_token` before either writes. The loser's
    `reset_password` re-checks under a lock and refuses; nothing of its is written."""
    token = _token(user)
    first_user = PasswordResetService.validate_token(token)
    second_user = PasswordResetService.validate_token(token)
    assert first_user is not None and second_user is not None

    PasswordResetService.reset_password(first_user, 'firstpass123', token=token)
    with pytest.raises(InvalidResetTokenError):
        PasswordResetService.reset_password(second_user, 'secondpass123', token=token)

    after = User.objects.get(pk=user.pk)
    assert after.check_password('firstpass123')
    assert not after.check_password('secondpass123')


def test_losing_the_race_leaves_the_email_unconfirmed_and_the_other_effects_alone(user):
    user.email_confirmed = False
    user.email_confirmed_at = None
    user.save()
    token = _token(user)
    stale = PasswordResetService.validate_token(token)
    assert stale is not None
    # Someone else changes the password after the page loaded (here: the account page).
    change_password(User.objects.get(pk=user.pk), 'changedpass123')

    with pytest.raises(InvalidResetTokenError):
        PasswordResetService.reset_password(stale, 'hijackpass123', token=token)

    after = User.objects.get(pk=user.pk)
    assert after.check_password('changedpass123')
    assert after.email_confirmed is False


def test_a_garbled_token_is_refused_by_reset_password(user):
    with pytest.raises(InvalidResetTokenError):
        PasswordResetService.reset_password(user, 'newpass123', token='not-a-token')


def test_page_and_api_answer_a_lost_race_like_an_invalid_link(
    client, api_client, user, monkeypatch
):
    """The view and the endpoint pass `validate_token`, then lose the race inside
    `reset_password`: the page bounces to the expired-link page, the API answers 400."""
    real_reset = PasswordResetService.reset_password
    winners = iter(['winnerpass123', 'winnertwo123'])

    def racing(user_, password, *, token):
        # Another request uses the same link between this one's validation and its write.
        real_reset(user_, next(winners), token=token)
        return real_reset(user_, password, token=token)

    monkeypatch.setattr(PasswordResetService, 'reset_password', staticmethod(racing))

    page_token = _token(user)
    posted = client.post(_url(page_token), _passwords('loserpass123'), **HTMX)
    assert posted['HX-Redirect'] == _url(page_token)
    assert User.objects.get(pk=user.pk).check_password('winnerpass123')

    api_token = _token(User.objects.get(pk=user.pk))
    assert _api_reset(api_client, api_token, 'loserpass123').status_code == 400
    after = User.objects.get(pk=user.pk)
    assert after.check_password('winnertwo123')
    assert not after.check_password('loserpass123')


def test_finish_setup_links_from_repeated_signup_attempts(
    user, monkeypatch, client, django_capture_on_commit_callbacks
):
    """Through the real path: someone signs up with an unconfirmed account's address twice. The
    first attempt locks the account down (the password becomes unusable); the second doesn't
    change the hash, so both emails' links stay valid until one is used -- and then both die."""
    user.email_confirmed = False
    user.email_confirmed_at = None
    user.save()
    sent: list[dict] = []
    monkeypatch.setattr(
        'apps.accounts.services.existing_account.send_email',
        lambda **kwargs: sent.append(kwargs) or (1, 0),
    )

    for _ in range(2):
        with django_capture_on_commit_callbacks(execute=True):
            handle_signup_for_existing_email(user.email, 'intruder123')

    assert not User.objects.get(pk=user.pk).has_usable_password()
    first, second = (_link_path(m) for m in sent)
    assert first != second

    # The first link still works after the second attempt ...
    assert client.get(first).status_code == 200
    # ... and using the second one is what kills the first.
    done = client.post(second, _passwords('chosenpass123'), **HTMX)
    assert done['HX-Redirect'] == reverse('accounts:login')
    assert 'expired' in client.get(first).content.decode().lower()
    again = client.post(first, _passwords('otherpass123'), **HTMX)
    assert again['HX-Redirect'] == first
    after = User.objects.get(pk=user.pk)
    assert after.check_password('chosenpass123')
    assert not after.check_password('otherpass123')
    assert after.email_confirmed is True


def test_token_for_an_empty_password_does_not_crash(user):
    User.objects.filter(pk=user.pk).update(password='')
    user.refresh_from_db()

    assert PasswordResetService.validate_token(_token(user)) is not None
