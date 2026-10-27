"""The account page's "Appearance" form (issue #166) and how `core/base.html` applies the theme."""

import json
import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services.theme import set_theme

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}
THEMES = ['system', 'light', 'dark']


def _html_tag(response) -> str:
    match = re.search(r'<html[^>]*>', response.content.decode())
    assert match is not None
    return match.group(0)


def _checked_themes(content: str) -> list[str]:
    """The values of the theme radios that are checked, in page order."""
    inputs = re.findall(r'<input type="radio"[^>]*>', content, flags=re.DOTALL)
    return [
        value for tag in inputs if 'checked' in tag for value in re.findall(r'value="(.*?)"', tag)
    ]


# --- the model and the service ---


def test_theme_defaults_to_system(user):
    user.refresh_from_db()
    assert user.theme == 'system'


def test_theme_column_has_a_database_default():
    """So a container of the previous release, which doesn't know the column, can still insert
    users while the migration runs."""
    assert User._meta.get_field('theme').db_default == 'system'  # ty: ignore[unresolved-attribute]


@pytest.mark.parametrize('theme', THEMES)
def test_set_theme_saves(user, theme):
    set_theme(user, theme)

    user.refresh_from_db()
    assert user.theme == theme


def test_set_theme_bumps_updated_at(user):
    """`save(update_fields=...)` skips `auto_now` unless `updated_at` is named, as the linking
    saves do."""
    before = User.objects.get(pk=user.pk).updated_at

    set_theme(user, 'dark')

    assert User.objects.get(pk=user.pk).updated_at > before


def test_set_theme_rejects_an_unknown_value(user):
    with pytest.raises(ValueError):
        set_theme(user, 'purple')

    user.refresh_from_db()
    assert user.theme == 'system'


# --- the account page ---


def test_account_page_shows_the_appearance_section(client, user):
    client.force_login(user)

    response = client.get(reverse('accounts:account'))

    content = response.content.decode()
    assert response.status_code == 200
    assert 'Appearance' in content
    assert f'hx-post="{reverse("accounts:theme-update")}"' in content
    for theme in THEMES:
        assert f'name="theme" value="{theme}"' in re.sub(r'\s+', ' ', content)


def test_theme_form_is_one_request_at_a_time_with_the_last_click_winning(client, user):
    """Quick clicks must not overlap: `hx-sync` queues, and drops all but the last."""
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()
    form = re.search(r'<form id="theme-form".*?>', content, flags=re.DOTALL)

    assert form is not None
    assert 'hx-sync="this:queue last"' in form.group(0)
    assert 'hx-trigger="change"' in form.group(0)


def test_account_page_checks_system_by_default(client, user):
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert _checked_themes(content) == ['system']


@pytest.mark.parametrize('theme', THEMES)
def test_account_page_checks_the_saved_theme(client, user, theme):
    set_theme(user, theme)
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert _checked_themes(content) == [theme]


def test_appearance_section_is_shown_to_an_account_without_a_password(client, user):
    user.set_unusable_password()
    user.save()
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert 'Appearance' in content
    assert 'Change Password' not in content


# --- saving ---


@pytest.mark.parametrize('theme', THEMES)
def test_htmx_save_answers_the_partial_without_swapping_it_and_announces_the_theme(
    client, user, theme
):
    # Start from a different theme, so every parametrized value is a change.
    set_theme(user, 'dark' if theme == 'light' else 'light')
    client.force_login(user)

    response = client.post(reverse('accounts:theme-update'), {'theme': theme}, **HTMX)

    assert response.status_code == 200
    # No reload: the page applies the theme itself, from the event.
    assert 'HX-Refresh' not in response
    assert json.loads(response['HX-Trigger']) == {'themeChanged': {'theme': theme}}
    # Swapping the radios would drop the keyboard focus from the one the user is on.
    assert response['HX-Reswap'] == 'none'
    content = response.content.decode()
    assert '<html' not in content
    assert 'id="theme-form"' in content
    assert _checked_themes(content) == [theme]
    user.refresh_from_db()
    assert user.theme == theme


@pytest.mark.parametrize('theme', THEMES)
def test_without_htmx_saves_and_redirects_to_the_account_page(client, user, theme):
    client.force_login(user)

    response = client.post(reverse('accounts:theme-update'), {'theme': theme})

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:account')
    user.refresh_from_db()
    assert user.theme == theme


@pytest.mark.parametrize('value', ['purple', 'DARK', 'dark ', '', '<script>'])
def test_invalid_value_is_a_422_and_saves_nothing(client, user, value):
    set_theme(user, 'light')
    client.force_login(user)

    response = client.post(reverse('accounts:theme-update'), {'theme': value}, **HTMX)

    assert response.status_code == 422
    assert 'HX-Refresh' not in response
    assert 'HX-Trigger' not in response
    # An error is swapped in over the form (no `HX-Reswap`), so the message shows.
    assert 'HX-Reswap' not in response
    content = response.content.decode()
    assert '<html' not in content
    assert 'Please choose' in content
    assert '<script>' not in content
    # The saved theme is still the one shown as selected.
    assert _checked_themes(content) == ['light']
    user.refresh_from_db()
    assert user.theme == 'light'


def test_missing_value_is_a_422_and_saves_nothing(client, user):
    client.force_login(user)

    response = client.post(reverse('accounts:theme-update'), {}, **HTMX)

    assert response.status_code == 422
    assert 'Please choose a theme.' in response.content.decode()
    user.refresh_from_db()
    assert user.theme == 'system'


def test_invalid_value_without_htmx_rerenders_the_account_page(client, user):
    client.force_login(user)

    response = client.post(reverse('accounts:theme-update'), {'theme': 'purple'})

    assert response.status_code == 422
    content = response.content.decode()
    assert '<html' in content
    assert 'Appearance' in content
    assert 'Please choose one of system, light or dark.' in content
    # The page is rendered whole, so the other sections' forms are there too.
    assert 'Profile Information' in content
    assert 'Change Password' in content
    user.refresh_from_db()
    assert user.theme == 'system'


def test_only_changes_the_logged_in_users_theme(client, user):
    other = User.objects.create_user('other@example.com', 'testpass123')
    client.force_login(user)

    client.post(reverse('accounts:theme-update'), {'theme': 'dark'}, **HTMX)

    other.refresh_from_db()
    assert other.theme == 'system'


def test_requires_login(client):
    response = client.post(reverse('accounts:theme-update'), {'theme': 'dark'})

    assert response.status_code == 302
    assert response['Location'].startswith(reverse('accounts:login'))


def test_is_post_only(client, user):
    client.force_login(user)

    for method in (client.get, client.put, client.delete):
        assert method(reverse('accounts:theme-update')).status_code == 405
    user.refresh_from_db()
    assert user.theme == 'system'


def test_requires_a_csrf_token(user):
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    response = client.post(reverse('accounts:theme-update'), {'theme': 'dark'}, **HTMX)

    assert response.status_code == 403
    user.refresh_from_db()
    assert user.theme == 'system'


def test_the_form_on_the_page_carries_a_csrf_token(user):
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    page = client.get(reverse('accounts:account')).content.decode()
    form = re.search(r'<form id="theme-form".*?</form>', page, flags=re.DOTALL)
    assert form is not None
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', form.group(0))
    assert token is not None

    response = client.post(
        reverse('accounts:theme-update'),
        {'theme': 'dark', 'csrfmiddlewaretoken': token.group(1)},
        **HTMX,
    )

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.theme == 'dark'


# --- the user admin ---


def test_admin_change_form_shows_the_theme_read_only(client, admin_user, regular_user):
    set_theme(regular_user, 'dark')
    client.force_login(admin_user)

    response = client.get(reverse('custom_admin:accounts_user_change', args=[regular_user.pk]))

    content = response.content.decode()
    assert response.status_code == 200
    assert 'field-theme' in content
    assert 'Dark' in content
    assert 'name="theme"' not in content


def test_admin_change_form_save_leaves_the_theme_alone(client, admin_user, regular_user):
    set_theme(regular_user, 'dark')
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:accounts_user_change', args=[regular_user.pk]),
        {
            'email': regular_user.email,
            'name': 'Renamed',
            'status': User.STATUS_ACTIVE,
            'is_active': 'on',
            'theme': 'light',
        },
    )

    assert response.status_code == 302
    regular_user.refresh_from_db()
    assert regular_user.name == 'Renamed'
    assert regular_user.theme == 'dark'


# --- how base.html applies it ---


@pytest.mark.parametrize(
    ('theme', 'is_dark'), [('system', False), ('light', False), ('dark', True)]
)
def test_base_renders_the_users_theme_on_the_html_element(client, user, theme, is_dark):
    set_theme(user, theme)
    client.force_login(user)

    tag = _html_tag(client.get(reverse('accounts:account')))

    assert f'data-theme="{theme}"' in tag
    # Only "dark" is a fixed class, rendered by the server so there is no flash before the boot
    # script runs; "system" is resolved by that script from the browser's colour scheme.
    assert ('class="dark"' in tag) is is_dark


def test_the_theme_applies_on_every_page_not_just_the_account_page(client, user):
    set_theme(user, 'dark')
    client.force_login(user)

    tag = _html_tag(client.get(reverse('core:home')))

    assert 'data-theme="dark"' in tag
    assert 'class="dark"' in tag


@pytest.mark.parametrize('url_name', ['core:home', 'accounts:login', 'accounts:register'])
def test_anonymous_visitors_follow_the_system(client, url_name):
    tag = _html_tag(client.get(reverse(url_name)))

    assert 'data-theme="system"' in tag
    assert 'class="dark"' not in tag


def test_anonymous_visitor_is_not_given_a_users_theme(client, user):
    """A signed-out browser doesn't inherit what the previous user of it chose."""
    set_theme(user, 'dark')
    client.force_login(user)
    client.post(reverse('accounts:logout'))

    tag = _html_tag(client.get(reverse('core:home')))

    assert 'data-theme="system"' in tag
    assert 'class="dark"' not in tag


def test_base_listens_for_the_theme_changed_event(client, user):
    """The success response only carries `HX-Trigger`; this listener is what applies it, on every
    page, resolving "system" itself and reading `data-theme` each time, so the colour-scheme
    listener only matters while the theme is "system"."""
    client.force_login(user)

    for url in (reverse('accounts:account'), reverse('core:home')):
        content = client.get(url).content.decode()
        assert "addEventListener('themeChanged'" in content
        assert "html.setAttribute('data-theme', theme)" in content
        assert "html.getAttribute('data-theme')" in content
        assert 'HX-Refresh' not in content


def test_the_header_toggle_and_browser_storage_are_gone(client, user):
    client.force_login(user)

    for url in (reverse('core:home'), reverse('accounts:account')):
        content = client.get(url).content.decode()
        assert 'theme-toggle' not in content
        assert 'localStorage' not in content
