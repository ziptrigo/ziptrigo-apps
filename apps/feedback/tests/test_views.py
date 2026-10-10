import pytest
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory

from ..models import MAX_DESCRIPTION_LENGTH, Feedback, FeedbackStatus
from ..views.feedback import SUBMITTED_SESSION_KEY

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def _mark_submitted(client):
    session = client.session
    session[SUBMITTED_SESSION_KEY] = True
    session.save()


@pytest.mark.parametrize('name', ['feedback:submit', 'feedback:thanks'])
def test_anonymous_visitor_is_sent_to_login(client, name):
    url = reverse(name)

    response = client.get(url)

    assert response.status_code == 302
    assert response.url == f'{reverse("accounts:login")}?next={url}'


def test_anonymous_post_saves_nothing(client):
    response = client.post(reverse('feedback:submit'), {'description': 'Hello'})

    assert response.status_code == 302
    assert not Feedback.objects.exists()


def test_get_shows_the_form(client, user):
    client.force_login(user)

    response = client.get(reverse('feedback:submit'))

    html = response.content.decode()
    assert response.status_code == 200
    assert 'name="description"' in html
    assert f'A copy will be emailed to {user.email}.' in html
    assert f'/ {MAX_DESCRIPTION_LENGTH:,}' in html
    assert f'count > {MAX_DESCRIPTION_LENGTH}' in html
    assert html.count('id="feedback-msg"') == 1
    assert '[...' in html


def test_form_hides_the_copy_line_for_an_unconfirmed_address(client):
    unconfirmed = UserFactory(email_confirmed=False)
    client.force_login(unconfirmed)

    html = client.get(reverse('feedback:submit')).content.decode()

    assert 'name="description"' in html
    assert 'A copy will be emailed' not in html


def test_htmx_post_saves_and_redirects_to_thanks(client, user):
    client.force_login(user)

    response = client.post(reverse('feedback:submit'), {'description': ' Great site\r\n'}, **HTMX)

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('feedback:thanks')
    assert client.session[SUBMITTED_SESSION_KEY] is True
    feedback = Feedback.objects.get()
    assert feedback.created_by == user
    assert feedback.description == 'Great site'
    assert feedback.status == FeedbackStatus.NEW


def test_plain_post_redirects_to_thanks(client, user):
    client.force_login(user)

    response = client.post(reverse('feedback:submit'), {'description': 'Great site'})

    assert response.status_code == 302
    assert response.url == reverse('feedback:thanks')
    assert Feedback.objects.count() == 1


def test_invalid_post_returns_422_partial_and_saves_nothing(client, user, sent_emails):
    client.force_login(user)

    response = client.post(reverse('feedback:submit'), {'description': '   '}, **HTMX)

    html = response.content.decode()
    assert response.status_code == 422
    assert 'Please write your feedback.' in html
    assert 'border-red-500' in html
    assert '<html' not in html
    # Clears a rate-limit message left over from an earlier 429, out of band.
    assert html.count('id="feedback-msg"') == 1
    assert 'id="feedback-msg" hx-swap-oob="true"' in html
    assert not Feedback.objects.exists()
    assert sent_emails == []


def test_too_long_post_returns_422(client, user):
    client.force_login(user)

    response = client.post(
        reverse('feedback:submit'), {'description': 'x' * (MAX_DESCRIPTION_LENGTH + 1)}, **HTMX
    )

    assert response.status_code == 422
    assert not Feedback.objects.exists()


def test_invalid_post_without_htmx_renders_the_full_page(client, user):
    client.force_login(user)

    response = client.post(reverse('feedback:submit'), {'description': ''})

    html = response.content.decode()
    assert response.status_code == 422
    assert '<html' in html
    assert html.count('id="feedback-msg"') == 1
    assert 'hx-swap-oob' not in html


def test_over_the_rate_limit_returns_429(client, user, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FEEDBACK_USER': (1, 60)}
    client.force_login(user)
    url = reverse('feedback:submit')

    first = client.post(url, {'description': 'one'}, **HTMX)
    second = client.post(url, {'description': 'two'}, **HTMX)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second['HX-Retarget'] == '#feedback-msg'
    assert 'Retry-After' in second
    assert Feedback.objects.count() == 1


def test_rejected_submissions_do_not_spend_the_rate_limit(client, user, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FEEDBACK_USER': (1, 60)}
    client.force_login(user)
    url = reverse('feedback:submit')

    for _ in range(3):
        assert client.post(url, {'description': ''}, **HTMX).status_code == 422
    response = client.post(url, {'description': 'finally'}, **HTMX)

    assert response.status_code == 200
    assert Feedback.objects.count() == 1


def test_thanks_page_shows_the_users_email(client, user):
    client.force_login(user)
    _mark_submitted(client)

    response = client.get(reverse('feedback:thanks'))

    html = response.content.decode()
    assert response.status_code == 200
    assert 'Thanks for your feedback!' in html
    assert f'emailed a copy to <strong>{user.email}</strong>' in html
    assert reverse('feedback:submit') in html
    assert reverse('core:home') in html


def test_thanks_page_omits_the_copy_line_for_an_unconfirmed_address(client):
    client.force_login(UserFactory(email_confirmed=False))
    _mark_submitted(client)

    html = client.get(reverse('feedback:thanks')).content.decode()

    assert "We've received your message." in html
    assert 'emailed a copy' not in html


def test_thanks_page_without_submitting_redirects_to_the_form(client, user):
    client.force_login(user)

    response = client.get(reverse('feedback:thanks'))

    assert response.status_code == 302
    assert response.url == reverse('feedback:submit')


def test_thanks_page_can_only_be_seen_once_per_submission(client, user):
    client.force_login(user)
    client.post(reverse('feedback:submit'), {'description': 'Great site'})

    first = client.get(reverse('feedback:thanks'))
    second = client.get(reverse('feedback:thanks'))

    assert first.status_code == 200
    assert second.status_code == 302
    assert second.url == reverse('feedback:submit')


def test_rate_limited_submission_does_not_unlock_the_thanks_page(client, user, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FEEDBACK_USER': (1, 60)}
    client.force_login(user)
    url = reverse('feedback:submit')
    client.post(url, {'description': 'one'}, **HTMX)
    client.get(reverse('feedback:thanks'))  # consume the first submission's flag

    assert client.post(url, {'description': 'two'}, **HTMX).status_code == 429

    assert client.get(reverse('feedback:thanks')).status_code == 302


def test_post_to_thanks_is_not_allowed(client, user):
    client.force_login(user)

    assert client.post(reverse('feedback:thanks')).status_code == 405
