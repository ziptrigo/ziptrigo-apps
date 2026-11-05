"""The date and time format a user picks on the account page (issue #245): the two columns, the
service, the form, the view, the API, the admin, and the middleware that applies them."""

import re

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.forms import DisplayFormatsForm
from apps.accounts.middleware import TimezoneMiddleware
from apps.accounts.models import User
from apps.accounts.services.display_formats import set_display_formats
from apps.accounts.tokens import CustomAccessToken
from apps.core import datetime_format

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def _checked(content: str, name: str) -> list[str]:
    """The values of the hidden inputs called `name` (what the themed dropdown submits), in page order."""
    tags = re.findall(r'<input type="hidden"[^>]*>', content, flags=re.DOTALL)
    return [
        value
        for tag in tags
        if f'name="{name}"' in tag
        for value in re.findall(r'value="(.*?)"', tag)
    ]


# --- the model and the service ---


def test_defaults_are_month_first_and_twelve_hour(user):
    user.refresh_from_db()
    assert (user.date_format, user.time_format) == ('mdy', '12h')


def test_columns_have_a_database_default():
    """So a container of the previous release, which doesn't know the columns, can still insert
    users while the migration runs."""
    assert User._meta.get_field('date_format').db_default == 'mdy'  # ty: ignore[unresolved-attribute]
    assert User._meta.get_field('time_format').db_default == '12h'  # ty: ignore[unresolved-attribute]


def test_set_display_formats_saves_both(user):
    assert set_display_formats(user, date_format='dmy', time_format='24h') is True

    user.refresh_from_db()
    assert (user.date_format, user.time_format) == ('dmy', '24h')


def test_none_leaves_a_field_alone(user):
    set_display_formats(user, date_format='dmy', time_format='24h')

    assert set_display_formats(user, time_format='12h') is True

    user.refresh_from_db()
    assert (user.date_format, user.time_format) == ('dmy', '12h')


def test_an_unchanged_value_writes_nothing(user):
    before = User.objects.get(pk=user.pk).updated_at

    assert set_display_formats(user, date_format='mdy', time_format='12h') is False
    assert set_display_formats(user) is False

    assert User.objects.get(pk=user.pk).updated_at == before


def test_only_the_changed_column_is_written(user, django_assert_num_queries):
    with django_assert_num_queries(1):
        set_display_formats(user, date_format='dmy', time_format='12h')


def test_a_change_bumps_updated_at(user):
    before = User.objects.get(pk=user.pk).updated_at

    set_display_formats(user, date_format='dmy')

    assert User.objects.get(pk=user.pk).updated_at > before


@pytest.mark.parametrize(
    'kwargs', [{'date_format': 'ymd'}, {'time_format': '25h'}, {'date_format': ''}]
)
def test_an_unknown_value_is_a_programming_error(user, kwargs):
    with pytest.raises(ValueError):
        set_display_formats(user, **kwargs)

    user.refresh_from_db()
    assert (user.date_format, user.time_format) == ('mdy', '12h')


# --- the form ---


def test_form_accepts_one_field_or_both():
    assert DisplayFormatsForm({'date_format': 'dmy'}).is_valid()
    assert DisplayFormatsForm({'time_format': '24h'}).is_valid()
    assert DisplayFormatsForm({'date_format': 'mdy', 'time_format': '12h'}).is_valid()


def test_form_refuses_an_empty_post_and_unknown_values():
    assert not DisplayFormatsForm({}).is_valid()
    assert not DisplayFormatsForm({'date_format': 'yyyy'}).is_valid()
    assert not DisplayFormatsForm({'time_format': '9h'}).is_valid()


# --- the view ---


def test_htmx_save_answers_200_without_a_swap(client, user):
    client.force_login(user)

    response = client.post(reverse('accounts:formats'), {'date_format': 'dmy'}, **HTMX)

    assert response.status_code == 200
    assert response['HX-Reswap'] == 'none'
    user.refresh_from_db()
    assert (user.date_format, user.time_format) == ('dmy', '12h')


def test_only_the_posted_field_is_written(client, user):
    set_display_formats(user, date_format='dmy', time_format='24h')
    client.force_login(user)

    client.post(reverse('accounts:formats'), {'time_format': '12h'}, **HTMX)

    user.refresh_from_db()
    assert (user.date_format, user.time_format) == ('dmy', '12h')


def test_without_htmx_it_redirects_to_the_account_page(client, user):
    client.force_login(user)

    response = client.post(reverse('accounts:formats'), {'time_format': '24h'})

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:account')
    user.refresh_from_db()
    assert user.time_format == '24h'


def test_a_bad_value_is_a_422_partial_for_htmx_and_saves_nothing(client, user):
    client.force_login(user)

    response = client.post(reverse('accounts:formats'), {'date_format': 'ymd'}, **HTMX)

    assert response.status_code == 422
    assert 'data-formats-error' in response.content.decode()
    assert '<html' not in response.content.decode()
    user.refresh_from_db()
    assert user.date_format == 'mdy'


def test_an_empty_post_is_a_422(client, user):
    client.force_login(user)

    response = client.post(reverse('accounts:formats'), {}, **HTMX)

    assert response.status_code == 422
    assert 'Please choose a date or a time format.' in response.content.decode()


def test_a_bad_value_without_htmx_re_renders_the_whole_page(client, user):
    client.force_login(user)

    response = client.post(reverse('accounts:formats'), {'time_format': '9h'})

    assert response.status_code == 422
    assert '<html' in response.content.decode()
    user.refresh_from_db()
    assert user.time_format == '12h'


def test_login_is_required(client):
    response = client.post(reverse('accounts:formats'), {'date_format': 'dmy'})

    assert response.status_code == 302
    assert reverse('accounts:login') in response['Location']


def test_get_is_not_allowed(client, user):
    client.force_login(user)

    assert client.get(reverse('accounts:formats')).status_code == 405


# --- the account page ---


def test_account_page_renders_both_dropdowns_with_the_defaults_selected(client, user):
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert _checked(content, 'date_format') == ['mdy']
    assert _checked(content, 'time_format') == ['12h']
    flat = re.sub(r'\s+', ' ', content)
    assert 'mm/dd/yyyy' in flat and 'dd/mm/yyyy' in flat
    assert '12-hour' in flat and '24-hour' in flat
    assert '(9:05 PM)' in flat and '(21:05)' in flat
    # Dropdowns, not radios: the site's own listbox, with a native select only for no JavaScript.
    assert 'type="radio"' not in flat.split('formats-form', 1)[1].split('</form>', 1)[0]
    assert flat.count('role="listbox"') >= 2
    assert '<noscript> <select name="date_format"' in flat


def test_account_page_selects_the_saved_choices(client, user):
    set_display_formats(user, date_format='dmy', time_format='24h')
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert _checked(content, 'date_format') == ['dmy']
    assert _checked(content, 'time_format') == ['24h']


def test_the_form_saves_on_change_one_request_at_a_time(client, user):
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()
    form = re.search(r'<form id="formats-form".*?>', content, flags=re.DOTALL)

    assert form is not None
    assert 'hx-trigger="change"' in form.group(0)
    assert 'hx-sync="this:queue last"' in form.group(0)
    assert f'hx-post="{reverse("accounts:formats")}"' in form.group(0)


def test_the_examples_are_todays_date_in_each_format(client, user):
    client.force_login(user)

    content = re.sub(r'\s+', ' ', client.get(reverse('accounts:account')).content.decode())

    today = datetime_format.format_date(
        timezone.localdate(), 'full', datetime_format.DisplayFormats('mdy', '12h')
    )
    other = datetime_format.format_date(
        timezone.localdate(), 'full', datetime_format.DisplayFormats('dmy', '12h')
    )
    assert f'({today})' in content and f'({other})' in content


# --- the API ---


def _bearer(user) -> str:
    return f'Bearer {CustomAccessToken.for_user(user)}'


def test_get_account_returns_the_formats(client, user):
    set_display_formats(user, date_format='dmy', time_format='24h')

    body = client.get('/api/account', HTTP_AUTHORIZATION=_bearer(user)).json()

    assert (body['date_format'], body['time_format']) == ('dmy', '24h')


def test_put_account_round_trips_both(client, user):
    response = client.put(
        '/api/account',
        {'date_format': 'dmy', 'time_format': '24h'},
        content_type='application/json',
        HTTP_AUTHORIZATION=_bearer(user),
    )

    assert response.status_code == 200
    assert response.json()['user']['date_format'] == 'dmy'
    assert response.json()['user']['time_format'] == '24h'
    user.refresh_from_db()
    assert (user.date_format, user.time_format) == ('dmy', '24h')


def test_put_account_takes_one_field_alone_and_needs_no_password(client, user):
    set_display_formats(user, date_format='dmy')

    response = client.put(
        '/api/account',
        {'time_format': '24h'},
        content_type='application/json',
        HTTP_AUTHORIZATION=_bearer(user),
    )

    assert response.status_code == 200
    user.refresh_from_db()
    assert (user.date_format, user.time_format) == ('dmy', '24h')


@pytest.mark.parametrize('payload', [{'date_format': 'ymd'}, {'time_format': '13h'}])
def test_put_account_refuses_an_unknown_value(client, user, payload):
    response = client.put(
        '/api/account',
        payload,
        content_type='application/json',
        HTTP_AUTHORIZATION=_bearer(user),
    )

    assert response.status_code == 422
    user.refresh_from_db()
    assert (user.date_format, user.time_format) == ('mdy', '12h')


# --- the admin ---


def test_admin_shows_both_read_only(client, admin_user, regular_user):
    client.force_login(admin_user)

    response = client.get(reverse('custom_admin:accounts_user_change', args=[regular_user.pk]))

    content = response.content.decode()
    assert response.status_code == 200
    assert 'name="date_format"' not in content
    assert 'name="time_format"' not in content
    assert 'Date format' in content and 'Time format' in content


# --- the middleware ---


def _run(user, path='/anything/') -> tuple[datetime_format.DisplayFormats, HttpResponse]:
    """Run `TimezoneMiddleware` for `user` and capture the formats active inside it."""
    seen = []

    def view(request):
        seen.append(datetime_format.get_active())
        return HttpResponse('ok')

    request = RequestFactory().get(path)
    request.user = user
    response = TimezoneMiddleware(view)(request)
    return seen[0], response


def test_a_signed_in_user_gets_their_formats(user):
    set_display_formats(user, date_format='dmy', time_format='24h')

    seen, _ = _run(user)

    assert seen == datetime_format.DisplayFormats('dmy', '24h')


def test_an_anonymous_visitor_gets_the_public_wording():
    from django.contrib.auth.models import AnonymousUser

    seen, _ = _run(AnonymousUser())

    assert seen is datetime_format.PUBLIC


@pytest.mark.parametrize('path', ['/t/abc/', '/s/abc', '/go/abc', '/static/x.js', '/api/account'])
def test_public_paths_get_the_public_wording_even_for_a_signed_in_user(user, path):
    set_display_formats(user, date_format='dmy', time_format='24h')

    seen, _ = _run(user, path)

    assert seen is datetime_format.PUBLIC


def test_the_formats_are_deactivated_after_the_response(user):
    set_display_formats(user, date_format='dmy', time_format='24h')

    _run(user)

    assert datetime_format.get_active() is datetime_format.PUBLIC
