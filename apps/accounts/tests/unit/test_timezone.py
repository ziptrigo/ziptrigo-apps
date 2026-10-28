"""A user's time zone (issue #177): the field, the service, the middleware, the account form, the
browser-detection endpoint and `PUT`/`GET /api/account`."""

import logging
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.template import Context, Template
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone as dj_timezone

from apps.accounts.forms import ProfileForm
from apps.accounts.middleware import TimezoneMiddleware
from apps.accounts.models import User
from apps.accounts.services import timezones
from apps.accounts.services.timezones import (
    local_datetime,
    set_timezone,
    timezone_choices,
    user_today,
    user_tz,
)
from apps.accounts.signals import timezone_changed
from apps.accounts.tokens import CustomAccessToken

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


@pytest.fixture
def received():
    """Every `timezone_changed` signal sent during a test, as `(user id, old, new)`."""
    calls: list[tuple] = []

    def receiver(sender, user, old, new, **kwargs):
        calls.append((user.pk, old, new))

    timezone_changed.connect(receiver, weak=False)
    yield calls
    timezone_changed.disconnect(receiver)


@pytest.fixture(autouse=True)
def _no_active_zone():
    dj_timezone.deactivate()
    yield
    dj_timezone.deactivate()


# --- the field ---


def test_timezone_defaults_to_empty(user):
    user.refresh_from_db()
    assert user.timezone == ''


def test_timezone_column_has_a_database_default():
    """So a container of the previous release, which doesn't know the column, can still insert
    users while the migration runs."""
    assert User._meta.get_field('timezone').db_default == ''  # ty: ignore[unresolved-attribute]


def test_model_clean_accepts_empty_and_known_zones(user):
    for name in ('', 'UTC', 'Europe/Lisbon'):
        user.timezone = name
        user.clean()


def test_model_clean_rejects_an_unknown_zone(user):
    user.timezone = 'Mars/Olympus_Mons'

    with pytest.raises(ValidationError) as error:
        user.clean()

    assert error.value.message_dict == {'timezone': ['Unknown time zone.']}


# --- user_tz, user_today ---


def test_user_tz_is_utc_when_not_set(user):
    assert user_tz(user) == ZoneInfo('UTC')


def test_user_tz_is_the_saved_zone(user):
    user.timezone = 'America/New_York'
    assert user_tz(user) == ZoneInfo('America/New_York')


def test_user_tz_falls_back_to_utc_for_a_stale_name_and_warns_once(user, caplog):
    user.timezone = 'Mars/Olympus_Mons'

    with caplog.at_level(logging.WARNING, logger=timezones.logger.name):
        first = user_tz(user)
        second = user_tz(user)

    assert first == second == ZoneInfo('UTC')
    warnings = [r for r in caplog.records if 'Mars/Olympus_Mons' in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.parametrize('name', ['../etc/passwd', 'Europe', '\x00', 'a' * 64])
def test_user_tz_never_raises_for_garbage(user, name):
    user.timezone = name
    assert user_tz(user) == ZoneInfo('UTC')


def test_user_today_follows_the_users_zone(user, monkeypatch):
    # 23:30 UTC on the 26th is already the 27th in Auckland (UTC+13) and still the 26th in Lisbon.
    monkeypatch.setattr(dj_timezone, 'now', lambda: datetime(2026, 10, 26, 23, 30, tzinfo=UTC))

    user.timezone = 'Pacific/Auckland'
    assert user_today(user) == date(2026, 10, 27)
    user.timezone = 'Europe/Lisbon'
    assert user_today(user) == date(2026, 10, 26)
    user.timezone = ''
    assert user_today(user) == date(2026, 10, 26)


# --- local_datetime ---


def test_local_datetime_converts_to_utc(user):
    user.timezone = 'America/New_York'

    result = local_datetime(user, date(2026, 7, 1), time(9, 0))

    assert result == datetime(2026, 7, 1, 13, 0, tzinfo=UTC)
    assert result.utcoffset() == UTC.utcoffset(None)


def test_local_datetime_without_a_time_is_local_midnight(user):
    user.timezone = 'Europe/Lisbon'

    assert local_datetime(user, date(2026, 7, 1), None) == datetime(2026, 6, 30, 23, 0, tzinfo=UTC)


def test_local_datetime_for_an_unset_zone_is_utc(user):
    assert local_datetime(user, date(2026, 7, 1), time(9, 0)) == datetime(
        2026, 7, 1, 9, 0, tzinfo=UTC
    )


def test_local_datetime_resolves_a_dst_gap_forward(user):
    """Lisbon's clocks went from 01:00 to 02:00 on 2026-03-29, so 01:30 never happened. It is read
    with the offset in force before the change (fold=0), which lands on the instant of 02:30."""
    user.timezone = 'Europe/Lisbon'

    result = local_datetime(user, date(2026, 3, 29), time(1, 30))

    assert result == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
    assert result.astimezone(ZoneInfo('Europe/Lisbon')).time() == time(2, 30)


def test_local_datetime_just_after_the_gap_is_unaffected(user):
    user.timezone = 'Europe/Lisbon'

    # 02:30 exists in Lisbon that day (UTC+1 by then).
    assert local_datetime(user, date(2026, 3, 29), time(2, 30)) == datetime(
        2026, 3, 29, 1, 30, tzinfo=UTC
    )


def test_local_datetime_resolves_an_ambiguous_time_to_the_first_occurrence(user):
    """Lisbon's clocks went back from 02:00 to 01:00 on 2026-10-25, so 01:30 happened twice (at
    00:30 UTC in summer time, then at 01:30 UTC). The first one is used."""
    user.timezone = 'Europe/Lisbon'

    assert local_datetime(user, date(2026, 10, 25), time(1, 30)) == datetime(
        2026, 10, 25, 0, 30, tzinfo=UTC
    )


def test_local_datetime_gap_in_new_york(user):
    """The spec's 02:30: skipped on 2026-03-08 in New York, where it is read as 03:30 EDT."""
    user.timezone = 'America/New_York'

    result = local_datetime(user, date(2026, 3, 8), time(2, 30))

    assert result.astimezone(ZoneInfo('America/New_York')).time() == time(3, 30)


# --- timezone_choices ---


def test_timezone_choices_start_with_utc_and_hide_legacy_names():
    choices = timezone_choices()
    names = [name for name, _label in choices]

    assert names[0] == 'UTC'
    assert names.count('UTC') == 1
    assert 'Europe/Lisbon' in names
    assert 'America/Argentina/Buenos_Aires' in names
    assert names[1:] == sorted(names[1:])
    for legacy in ('GMT', 'CET', 'EST', 'Japan', 'Etc/UTC', 'US/Pacific', 'Canada/Eastern'):
        assert legacy not in names
    assert not [n for n in names if n.startswith(('Etc/', 'SystemV/', 'US/', 'posix/', 'right/'))]


def test_timezone_choices_show_underscores_as_spaces():
    assert ('America/New_York', 'America/New York') in timezone_choices()


# --- set_timezone ---


def test_set_timezone_saves_and_reports_a_change(
    user, django_capture_on_commit_callbacks, received
):
    with django_capture_on_commit_callbacks(execute=True):
        changed = set_timezone(user, 'Europe/Lisbon')

    assert changed is True
    assert user.timezone == 'Europe/Lisbon'
    assert User.objects.get(pk=user.pk).timezone == 'Europe/Lisbon'
    assert received == [(user.pk, '', 'Europe/Lisbon')]


def test_set_timezone_with_the_same_value_changes_nothing(
    user, django_capture_on_commit_callbacks, received
):
    set_timezone(user, 'Europe/Lisbon')

    with django_capture_on_commit_callbacks(execute=True):
        changed = set_timezone(user, 'Europe/Lisbon')

    assert changed is False
    assert received == []


def test_set_timezone_compares_with_the_database_not_the_instance(
    user, django_capture_on_commit_callbacks, received
):
    """A stale instance (another request saved a zone meanwhile) must not send a signal with a
    wrong `old`, or a second one for the same change."""
    stale = User.objects.get(pk=user.pk)
    set_timezone(user, 'Europe/Lisbon')
    received.clear()

    with django_capture_on_commit_callbacks(execute=True):
        assert set_timezone(stale, 'Europe/Lisbon') is False
        assert set_timezone(stale, 'Asia/Tokyo') is True

    assert received == [(user.pk, 'Europe/Lisbon', 'Asia/Tokyo')]


def test_set_timezone_only_if_unset_keeps_a_saved_zone(
    user, django_capture_on_commit_callbacks, received
):
    set_timezone(user, 'Europe/Lisbon')
    stale = User.objects.get(pk=user.pk)
    stale.timezone = ''  # an instance loaded before the zone was saved

    with django_capture_on_commit_callbacks(execute=True):
        changed = set_timezone(stale, 'Asia/Tokyo', only_if_unset=True)

    assert changed is False
    assert stale.timezone == 'Europe/Lisbon'
    assert User.objects.get(pk=user.pk).timezone == 'Europe/Lisbon'


def test_set_timezone_can_clear_the_zone(user):
    set_timezone(user, 'Europe/Lisbon')

    assert set_timezone(user, '') is True
    assert User.objects.get(pk=user.pk).timezone == ''


def test_set_timezone_rejects_an_unknown_zone(user, django_capture_on_commit_callbacks, received):
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(ValidationError, match='Unknown time zone'):
            set_timezone(user, 'Mars/Olympus_Mons')

    assert User.objects.get(pk=user.pk).timezone == ''
    assert received == []


def test_set_timezone_bumps_updated_at(user):
    before = User.objects.get(pk=user.pk).updated_at

    set_timezone(user, 'Asia/Tokyo')

    assert User.objects.get(pk=user.pk).updated_at > before


def test_a_failing_receiver_does_not_break_the_save(
    user, django_capture_on_commit_callbacks, caplog
):
    def broken(sender, **kwargs):
        raise RuntimeError('boom')

    timezone_changed.connect(broken, weak=False)
    try:
        with caplog.at_level(logging.ERROR, logger=timezones.logger.name):
            with django_capture_on_commit_callbacks(execute=True):
                assert set_timezone(user, 'Asia/Tokyo') is True
    finally:
        timezone_changed.disconnect(broken)

    assert User.objects.get(pk=user.pk).timezone == 'Asia/Tokyo'
    assert any('timezone_changed receiver' in r.getMessage() for r in caplog.records)


# --- the middleware ---


def _run_middleware(user=None) -> dict:
    """Run the middleware around a view that records what the current zone is while it runs."""
    seen: dict = {}

    def view(request):
        seen['zone'] = dj_timezone.get_current_timezone_name()
        seen['rendered'] = Template('{% now "O" %}').render(Context())
        return HttpResponse('ok')

    request = RequestFactory().get('/')
    if user is not None:
        request.user = user
    else:
        from django.contrib.auth.models import AnonymousUser

        request.user = AnonymousUser()
    TimezoneMiddleware(view)(request)
    seen['after'] = dj_timezone.get_current_timezone_name()
    return seen


def test_middleware_activates_the_users_zone(user):
    user.timezone = 'America/New_York'

    seen = _run_middleware(user)

    assert seen['zone'] == 'America/New_York'
    # `{% now "O" %}` is the current offset from UTC: -0400 or -0500, never +0000.
    assert seen['rendered'] in ('-0400', '-0500')


def test_middleware_deactivates_after_the_response(user):
    user.timezone = 'America/New_York'

    assert _run_middleware(user)['after'] == 'UTC'


def test_middleware_leaves_anonymous_visitors_on_utc():
    dj_timezone.activate(ZoneInfo('Asia/Tokyo'))  # a leftover from another request

    seen = _run_middleware()

    assert seen['zone'] == 'UTC'
    assert seen['rendered'] == '+0000'


def test_middleware_uses_utc_for_a_user_with_no_zone(user):
    assert _run_middleware(user)['zone'] == 'UTC'


def test_middleware_deactivates_even_when_the_view_raises(user):
    user.timezone = 'America/New_York'

    def view(request):
        raise RuntimeError('boom')

    request = RequestFactory().get('/')
    request.user = user
    with pytest.raises(RuntimeError):
        TimezoneMiddleware(view)(request)

    assert dj_timezone.get_current_timezone_name() == 'UTC'


def test_middleware_is_installed_right_after_authentication(settings):
    middleware = settings.MIDDLEWARE
    auth = middleware.index('django.contrib.auth.middleware.AuthenticationMiddleware')

    assert middleware[auth + 1] == 'apps.accounts.middleware.TimezoneMiddleware'


def test_a_real_request_runs_in_the_users_zone(client, user, monkeypatch):
    """Through the whole stack (the middleware is installed), and the zone is gone afterwards."""
    from apps.accounts.views import pages

    user.timezone = 'Pacific/Auckland'
    user.save()
    client.force_login(user)
    seen = []
    real_render = pages.render

    def render(*args, **kwargs):
        seen.append(dj_timezone.get_current_timezone_name())
        return real_render(*args, **kwargs)

    monkeypatch.setattr(pages, 'render', render)

    response = client.get(reverse('accounts:account'))

    assert response.status_code == 200
    assert seen == ['Pacific/Auckland']
    assert dj_timezone.get_current_timezone_name() == 'UTC'
    assert 'data-user-timezone="Pacific/Auckland"' in response.content.decode()


# --- the browser-side helpers keep working whatever zone is active ---


def test_local_time_tag_keeps_rendering_utc_when_a_zone_is_active():
    value = datetime(2026, 10, 27, 13, 51, tzinfo=UTC)
    template = Template('{% load local_time %}{% local_time value %}|{% local_time value "iso" %}')

    dj_timezone.activate(ZoneInfo('Asia/Tokyo'))
    html = template.render(Context({'value': value}))

    assert 'datetime="2026-10-27T13:51:00+00:00"' in html
    assert 'Oct 27, 2026 13:51 UTC' in html
    assert '2026-10-27 13:51 UTC' in html


def test_local_time_tag_converts_an_aware_value_in_another_zone_to_utc():
    value = datetime(2026, 10, 27, 22, 51, tzinfo=ZoneInfo('Asia/Tokyo'))
    template = Template('{% load local_time %}{% local_time value %}')

    dj_timezone.activate(ZoneInfo('America/New_York'))
    html = template.render(Context({'value': value}))

    assert 'datetime="2026-10-27T13:51:00+00:00"' in html
    assert '13:51 UTC' in html


# --- the account page's form ---


def _post_profile(client, **data):
    return client.post(reverse('accounts:profile-update'), data, **HTMX)


def test_account_page_has_a_timezone_select_with_utc_first(client, user):
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert 'name="timezone"' in content
    options = [line.strip() for line in content.splitlines() if line.strip().startswith('<option')]
    assert options[0] == '<option value="" selected>Not set (UTC)</option>'
    assert options[1] == '<option value="UTC">UTC</option>'
    assert '<option value="America/New_York">America/New York</option>' in options
    assert 'Detected from your browser' in content


def test_account_page_selects_the_saved_zone(client, user):
    set_timezone(user, 'Europe/Lisbon')
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert '<option value="Europe/Lisbon" selected>Europe/Lisbon</option>' in content
    assert 'Not set (UTC)' not in content


def test_account_page_offers_a_valid_legacy_zone_that_is_saved(client, user):
    set_timezone(user, 'US/Pacific')
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert '<option value="US/Pacific" selected>US/Pacific</option>' in content


def test_form_saves_a_valid_zone_and_fires_the_signal_once(
    client, user, django_capture_on_commit_callbacks, received
):
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        response = _post_profile(client, name='New Name', timezone='Europe/Lisbon')

    assert response.status_code == 200
    assert 'Profile updated successfully.' in response.content.decode()
    user.refresh_from_db()
    assert (user.name, user.timezone) == ('New Name', 'Europe/Lisbon')
    assert received == [(user.pk, '', 'Europe/Lisbon')]


def test_form_does_not_fire_the_signal_for_an_unchanged_zone(
    client, user, django_capture_on_commit_callbacks, received
):
    set_timezone(user, 'Europe/Lisbon')
    received.clear()
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        response = _post_profile(client, name='Other Name', timezone='Europe/Lisbon')

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.name == 'Other Name'
    assert received == []


def test_form_rejects_an_unknown_zone(client, user, django_capture_on_commit_callbacks, received):
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        response = _post_profile(client, name='New Name', timezone='Mars/Olympus_Mons')

    assert response.status_code == 422
    assert 'Unknown time zone.' in response.content.decode()
    user.refresh_from_db()
    assert (user.name, user.timezone) == ('Test User', '')
    assert received == []


def test_form_rejects_an_unknown_zone_without_htmx(client, user):
    client.force_login(user)

    response = client.post(
        reverse('accounts:profile-update'), {'name': 'New Name', 'timezone': 'Nowhere/Land'}
    )

    assert response.status_code == 422
    assert 'Unknown time zone.' in response.content.decode()
    user.refresh_from_db()
    assert user.timezone == ''


def test_form_with_an_empty_zone_clears_it(client, user):
    set_timezone(user, 'Europe/Lisbon')
    client.force_login(user)

    _post_profile(client, name='Test User', timezone='')

    user.refresh_from_db()
    assert user.timezone == ''


def test_form_without_a_timezone_field_keeps_the_saved_zone(client, user):
    set_timezone(user, 'Europe/Lisbon')
    client.force_login(user)

    response = _post_profile(client, name='New Name')

    assert response.status_code == 200
    user.refresh_from_db()
    assert (user.name, user.timezone) == ('New Name', 'Europe/Lisbon')


def test_form_commit_false_does_not_save_or_send(user, received):
    form = ProfileForm({'name': 'N', 'timezone': 'Asia/Tokyo'}, instance=user)
    assert form.is_valid()

    form.save(commit=False)

    assert User.objects.get(pk=user.pk).timezone == ''
    assert received == []


# --- POST /account/timezone/ ---


def _detect(client, name):
    return client.post(reverse('accounts:timezone'), {'timezone': name})


def test_detection_sets_an_empty_zone(client, user, django_capture_on_commit_callbacks, received):
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        response = _detect(client, 'Europe/Lisbon')

    assert response.status_code == 204
    assert User.objects.get(pk=user.pk).timezone == 'Europe/Lisbon'
    assert received == [(user.pk, '', 'Europe/Lisbon')]


def test_detection_never_overrides_a_saved_zone(client, user, received):
    set_timezone(user, 'Asia/Tokyo')
    received.clear()
    client.force_login(user)

    response = _detect(client, 'Europe/Lisbon')

    assert response.status_code == 204
    assert User.objects.get(pk=user.pk).timezone == 'Asia/Tokyo'
    assert received == []


def test_detection_does_not_override_a_zone_saved_since_the_request_loaded_the_user(
    client, user, monkeypatch
):
    """Two tabs: the account page saved a zone after this request read the user."""
    client.force_login(user)
    real = set_timezone

    def racing(user_, name, **kwargs):
        User.objects.filter(pk=user_.pk).update(timezone='Asia/Tokyo')
        return real(user_, name, **kwargs)

    monkeypatch.setattr('apps.accounts.views.timezone.set_timezone', racing)

    assert _detect(client, 'Europe/Lisbon').status_code == 204
    assert User.objects.get(pk=user.pk).timezone == 'Asia/Tokyo'


@pytest.mark.parametrize('name', ['', 'garbage', 'Mars/Olympus_Mons', '../etc/passwd', 'a' * 500])
def test_detection_rejects_garbage_with_a_400(client, user, name):
    client.force_login(user)

    response = _detect(client, name)

    assert response.status_code == 400
    assert response.json() == {'detail': 'Unknown time zone.'}
    assert User.objects.get(pk=user.pk).timezone == ''


def test_detection_without_the_field_is_a_400(client, user):
    client.force_login(user)

    assert client.post(reverse('accounts:timezone'), {}).status_code == 400


def test_detection_needs_login(client, user):
    response = _detect(client, 'Europe/Lisbon')

    assert response.status_code == 302
    assert response['Location'].startswith(reverse('accounts:login'))
    assert User.objects.get(pk=user.pk).timezone == ''


def test_detection_is_post_only(client, user):
    client.force_login(user)

    assert client.get(reverse('accounts:timezone')).status_code == 405


def test_detection_needs_the_csrf_token(user):
    from django.test import Client

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(user)

    assert _detect(csrf_client, 'Europe/Lisbon').status_code == 403
    assert User.objects.get(pk=user.pk).timezone == ''


def test_detection_is_rate_limited(client, user, settings, ratelimit_clock):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'ACCOUNT_TIMEZONE_USER': (2, 3600)}
    client.force_login(user)

    assert _detect(client, 'garbage').status_code == 400
    assert _detect(client, 'garbage').status_code == 400
    response = _detect(client, 'Europe/Lisbon')

    assert response.status_code == 429
    assert response['Retry-After'] == '2600'
    assert 'error' in response.json()
    assert User.objects.get(pk=user.pk).timezone == ''


def test_the_rule_exists_with_the_documented_limit(settings):
    assert settings.RATELIMIT_RULES['ACCOUNT_TIMEZONE_USER'] == (10, 60 * 60)


def test_pages_load_the_detection_script_only_for_signed_in_users(client, user):
    anonymous = client.get(reverse('accounts:login')).content.decode()
    client.force_login(user)
    signed_in = client.get(reverse('accounts:account')).content.decode()

    assert 'accounts/js/timezone.js' not in anonymous
    assert 'data-user-timezone' not in anonymous
    assert 'accounts/js/timezone.js' in signed_in
    assert f'data-url="{reverse("accounts:timezone")}"' in signed_in
    assert 'data-user-timezone=""' in signed_in


# --- the API ---


def _put(client, user, payload):
    return client.put(
        '/api/account',
        payload,
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {CustomAccessToken.for_user(user)}',
    )


def _get(client, user):
    return client.get(
        '/api/account', HTTP_AUTHORIZATION=f'Bearer {CustomAccessToken.for_user(user)}'
    )


def test_put_account_sets_the_zone_without_a_password(
    client, user, django_capture_on_commit_callbacks, received
):
    with django_capture_on_commit_callbacks(execute=True):
        response = _put(client, user, {'timezone': 'Asia/Tokyo'})

    assert response.status_code == 200
    assert response.json()['user']['timezone'] == 'Asia/Tokyo'
    user.refresh_from_db()
    assert user.timezone == 'Asia/Tokyo'
    assert received == [(user.pk, '', 'Asia/Tokyo')]


def test_put_account_rejects_an_unknown_zone_with_422(client, user):
    response = _put(client, user, {'timezone': 'Mars/Olympus_Mons', 'name': 'Changed'})

    assert response.status_code == 422
    assert 'Unknown time zone.' in str(response.json())
    user.refresh_from_db()
    assert user.timezone == ''
    assert user.name == 'Test User'  # nothing of the request was applied


def test_put_account_without_a_zone_leaves_it_alone(client, user):
    set_timezone(user, 'Asia/Tokyo')

    response = _put(client, user, {'name': 'New Name'})

    assert response.status_code == 200
    user.refresh_from_db()
    assert (user.name, user.timezone) == ('New Name', 'Asia/Tokyo')


def test_put_account_can_clear_the_zone(client, user):
    set_timezone(user, 'Asia/Tokyo')

    assert _put(client, user, {'timezone': ''}).status_code == 200
    user.refresh_from_db()
    assert user.timezone == ''


def test_get_account_returns_the_zone(client, user):
    set_timezone(user, 'Asia/Tokyo')

    response = _get(client, user)

    assert response.status_code == 200
    assert response.json() == {
        'id': str(user.id),
        'name': 'Test User',
        'email': 'testuser@example.com',
        'timezone': 'Asia/Tokyo',
    }


def test_get_account_needs_authentication(client):
    assert client.get('/api/account').status_code == 401


# --- the admin ---


def test_admin_change_form_edits_the_zone_and_sends_the_signal(
    client, admin_user, regular_user, django_capture_on_commit_callbacks, received
):
    client.force_login(admin_user)
    url = reverse('custom_admin:accounts_user_change', args=[regular_user.pk])
    page = client.get(url).content.decode()
    assert 'name="timezone"' in page

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            url,
            {
                'email': regular_user.email,
                'name': regular_user.name,
                'timezone': 'Asia/Tokyo',
                'status': User.STATUS_ACTIVE,
                'is_active': 'on',
            },
        )

    assert response.status_code == 302
    regular_user.refresh_from_db()
    assert regular_user.timezone == 'Asia/Tokyo'
    assert received == [(regular_user.pk, '', 'Asia/Tokyo')]


def test_admin_change_form_rejects_an_unknown_zone(client, admin_user, regular_user):
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:accounts_user_change', args=[regular_user.pk]),
        {
            'email': regular_user.email,
            'name': regular_user.name,
            'timezone': 'Mars/Olympus_Mons',
            'status': User.STATUS_ACTIVE,
            'is_active': 'on',
        },
    )

    assert response.status_code == 200
    assert 'Unknown time zone.' in response.content.decode()
    regular_user.refresh_from_db()
    assert regular_user.timezone == ''
