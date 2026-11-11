"""The colour scheme chosen under "Appearance" on the account page."""

import json
import re

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services.theme import set_color_scheme
from apps.core import color_schemes

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}
SCHEME_IDS = [scheme.id for scheme in color_schemes.all_schemes()]


def _html_tag(response) -> str:
    match = re.search(r'<html[^>]*>', response.content.decode())
    assert match is not None
    return match.group(0)


def _checked_schemes(content: str) -> list[str]:
    inputs = re.findall(r'<input type="radio"[^>]*>', content, flags=re.DOTALL)
    return [
        value
        for tag in inputs
        if 'name="color_scheme"' in tag and 'checked' in tag
        for value in re.findall(r'value="(.*?)"', tag)
    ]


# --- the model and the service ---


def test_color_scheme_defaults_to_sage(user):
    user.refresh_from_db()
    assert user.color_scheme == 'sage'


def test_color_scheme_column_has_a_database_default():
    assert User._meta.get_field('color_scheme').db_default == 'sage'  # ty: ignore[missing-attribute]


@pytest.mark.parametrize('scheme', SCHEME_IDS)
def test_set_color_scheme_saves(user, scheme):
    set_color_scheme(user, scheme)

    user.refresh_from_db()
    assert user.color_scheme == scheme


def test_set_color_scheme_rejects_an_unknown_value(user):
    with pytest.raises(ValueError):
        set_color_scheme(user, 'neon')

    user.refresh_from_db()
    assert user.color_scheme == 'sage'


# --- the account page ---


def test_account_page_offers_every_scheme_with_the_saved_one_checked(client, user):
    set_color_scheme(user, 'plum')
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert 'Color scheme' in content
    flat = re.sub(r'\s+', ' ', content)
    for scheme in color_schemes.all_schemes():
        assert f'name="color_scheme" value="{scheme.id}"' in flat
        assert scheme.label in content
    assert _checked_schemes(content) == ['plum']


def test_htmx_save_stores_the_scheme_and_tells_the_page(client, user):
    client.force_login(user)

    response = client.post(
        reverse('accounts:theme-update'), {'theme': 'dark', 'color_scheme': 'ember'}, **HTMX
    )

    assert response.status_code == 200
    assert response['HX-Reswap'] == 'none'
    user.refresh_from_db()
    assert (user.theme, user.color_scheme) == ('dark', 'ember')
    detail = json.loads(response['HX-Trigger'])['themeChanged']
    assert detail['theme'] == 'dark'
    assert detail['scheme']['id'] == 'ember'
    assert detail['scheme']['theme_color'] == '#e59d7e'
    assert detail['scheme']['logos']['32'].endswith('core/images/schemes/ember/logo_32x32.png')
    assert detail['scheme']['logos']['128'].endswith('core/images/schemes/ember/logo_128x128.png')


def test_a_post_without_a_scheme_keeps_the_saved_one(client, user):
    set_color_scheme(user, 'sky')
    client.force_login(user)

    response = client.post(reverse('accounts:theme-update'), {'theme': 'light'}, **HTMX)

    assert response.status_code == 200
    user.refresh_from_db()
    assert (user.theme, user.color_scheme) == ('light', 'sky')
    assert json.loads(response['HX-Trigger'])['themeChanged']['scheme']['id'] == 'sky'


def test_without_htmx_a_save_redirects_to_the_account_page(client, user):
    client.force_login(user)

    response = client.post(
        reverse('accounts:theme-update'), {'theme': 'system', 'color_scheme': 'lagoon'}
    )

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:account')
    user.refresh_from_db()
    assert user.color_scheme == 'lagoon'


def test_an_unknown_scheme_is_a_422_and_saves_nothing(client, user):
    client.force_login(user)

    response = client.post(
        reverse('accounts:theme-update'), {'theme': 'dark', 'color_scheme': 'neon'}, **HTMX
    )

    assert response.status_code == 422
    assert 'Please choose one of the listed color schemes.' in response.content.decode()
    user.refresh_from_db()
    assert (user.theme, user.color_scheme) == ('system', 'sage')


# --- how base.html applies it ---


@pytest.mark.parametrize('scheme', SCHEME_IDS)
def test_base_renders_the_users_scheme(client, user, scheme):
    set_color_scheme(user, scheme)
    client.force_login(user)

    response = client.get(reverse('core:home'))

    content = response.content.decode()
    picked = color_schemes.get_scheme(scheme)
    assert f'data-scheme="{scheme}"' in _html_tag(response)
    assert f'<meta name="theme-color" content="{picked.theme_color}">' in content
    assert content.count(picked.logo_path(32)) == 2  # favicon and header
    assert picked.logo_path(128) in content


def test_a_visitor_gets_sage(client):
    response = client.get(reverse('accounts:login'))

    assert 'data-scheme="sage"' in _html_tag(response)
    assert '<meta name="theme-color" content="#8fa89e">' in response.content.decode()


def test_a_stale_value_renders_as_sage_and_the_picker_checks_sage(client, user):
    User.objects.filter(pk=user.pk).update(color_scheme='retired')
    client.force_login(user)

    response = client.get(reverse('accounts:account'))

    assert 'data-scheme="sage"' in _html_tag(response)
    assert _checked_schemes(response.content.decode()) == ['sage']


def test_selected_theme_option_uses_the_primary_colours(client, user):
    """So it matches the primary button in a solid scheme (Midnight, Graphite)."""
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert 'peer-checked:bg-primary peer-checked:text-primary-on' in content


def test_color_scheme_is_read_only_in_the_user_admin(admin_client, user):
    response = admin_client.get(reverse('admin:accounts_user_change', args=[user.pk]))

    content = response.content.decode()
    assert 'Color scheme' in content
    assert 'name="color_scheme"' not in content
