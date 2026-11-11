"""The "High contrast" switch under "Appearance" on the account page."""

import json
import re

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services.theme import set_color_scheme, set_high_contrast, set_theme

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def _html_tag(response) -> str:
    match = re.search(r'<html[^>]*>', response.content.decode())
    assert match is not None
    return match.group(0)


def _checkbox(content: str) -> str:
    match = re.search(
        r'<input type="checkbox"\s+name="high_contrast"[^>]*>', content, flags=re.DOTALL
    )
    assert match is not None
    return match.group(0)


def test_high_contrast_is_off_by_default_with_a_database_default(user):
    user.refresh_from_db()
    assert user.high_contrast is False
    assert User._meta.get_field('high_contrast').db_default is False  # ty: ignore[missing-attribute]


@pytest.mark.parametrize('enabled', [True, False])
def test_set_high_contrast_saves(user, enabled):
    set_high_contrast(user, not enabled)
    set_high_contrast(user, enabled)

    user.refresh_from_db()
    assert user.high_contrast is enabled


def test_account_page_has_the_switch_next_to_the_themes(client, user):
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert 'High contrast' in content
    assert 'checked' not in _checkbox(content)
    assert '<input type="hidden" name="contrast_posted" value="1">' in content
    # After the Dark option, inside the theme group.
    assert (
        content.index('value="dark"')
        < content.index('name="high_contrast"')
        < content.index('Color scheme')
    )


def test_account_page_checks_the_switch_when_it_is_on(client, user):
    set_high_contrast(user, True)
    client.force_login(user)

    assert 'checked' in _checkbox(client.get(reverse('accounts:account')).content.decode())


def test_turning_it_on_saves_and_tells_the_page(client, user):
    client.force_login(user)

    response = client.post(
        reverse('accounts:theme-update'),
        {'theme': 'light', 'color_scheme': 'sky', 'contrast_posted': '1', 'high_contrast': 'on'},
        **HTMX,
    )

    assert response.status_code == 200
    user.refresh_from_db()
    assert (user.theme, user.color_scheme, user.high_contrast) == ('light', 'sky', True)
    assert json.loads(response['HX-Trigger'])['themeChanged']['high_contrast'] is True


def test_an_unticked_box_turns_it_off(client, user):
    set_high_contrast(user, True)
    client.force_login(user)

    response = client.post(
        reverse('accounts:theme-update'), {'theme': 'light', 'contrast_posted': '1'}, **HTMX
    )

    user.refresh_from_db()
    assert user.high_contrast is False
    assert json.loads(response['HX-Trigger'])['themeChanged']['high_contrast'] is False


def test_a_post_from_a_page_without_the_switch_leaves_it_alone(client, user):
    set_high_contrast(user, True)
    client.force_login(user)

    response = client.post(reverse('accounts:theme-update'), {'theme': 'dark'}, **HTMX)

    user.refresh_from_db()
    assert (user.theme, user.high_contrast) == ('dark', True)
    assert json.loads(response['HX-Trigger'])['themeChanged']['high_contrast'] is True


def test_a_refused_form_changes_nothing(client, user):
    client.force_login(user)

    response = client.post(
        reverse('accounts:theme-update'),
        {'theme': 'purple', 'contrast_posted': '1', 'high_contrast': 'on'},
        **HTMX,
    )

    assert response.status_code == 422
    user.refresh_from_db()
    assert user.high_contrast is False


@pytest.mark.parametrize('theme', ['system', 'light', 'dark'])
def test_base_renders_the_attribute_in_every_theme_and_scheme(client, user, theme):
    set_theme(user, theme)
    set_color_scheme(user, 'plum')
    set_high_contrast(user, True)
    client.force_login(user)

    tag = _html_tag(client.get(reverse('core:home')))

    assert 'data-contrast="high"' in tag
    assert 'data-scheme="plum"' in tag
    assert f'data-theme="{theme}"' in tag


def test_base_has_no_attribute_when_it_is_off_or_for_a_visitor(client, user):
    assert 'data-contrast' not in _html_tag(client.get(reverse('accounts:login')))

    client.force_login(user)
    assert 'data-contrast' not in _html_tag(client.get(reverse('core:home')))


def test_high_contrast_is_read_only_in_the_user_admin(admin_client, user):
    response = admin_client.get(reverse('admin:accounts_user_change', args=[user.pk]))

    content = response.content.decode()
    assert 'High contrast' in content
    assert 'name="high_contrast"' not in content
