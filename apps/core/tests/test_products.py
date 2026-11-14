"""The ProductApp registry and the nav/landing page built from it."""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import reverse

from apps.core.context_processors import products
from apps.core.products import ProductApp, get_products, products_for, register, visible_products

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_installed_products_are_registered_in_installed_apps_order():
    labels = [product.label for product in get_products()]

    assert labels == [
        'qr_code',
        'file_transfer',
        'receipts',
        'short_url',
        'todo',
        'notes',
        'world_time',
    ]


def test_every_product_url_resolves():
    for product in get_products():
        assert reverse(product.url_name)


def test_registering_a_label_again_replaces_it():
    original = next(p for p in get_products() if p.label == 'file_transfer')
    try:
        register(
            ProductApp(
                label='file_transfer',
                name='Renamed',
                description='',
                url_name=original.url_name,
                icon='',
            )
        )
        names = [p.name for p in get_products() if p.label == 'file_transfer']
        assert names == ['Renamed']
    finally:
        register(original)


def test_nav_and_landing_page_list_products(client):
    content = client.get(reverse('core:home')).content.decode()

    for product in get_products():
        assert product.name in content
        # Once in the nav, once on a landing-page card.
        assert content.count(f'href="{reverse(product.url_name)}"') == 2


def test_receipts_is_registered_as_a_product():
    receipts = next(p for p in get_products() if p.label == 'receipts')

    assert receipts.name == 'Receipts'
    assert receipts.description == (
        'Email or snap your receipts and invoices; we file and categorize them.'
    )
    assert receipts.url_name == 'receipts:dashboard'
    assert receipts.icon == 'fas fa-receipt'
    assert reverse(receipts.url_name) == '/receipts/'


def test_short_url_is_registered_as_a_product():
    short_url = next(p for p in get_products() if p.label == 'short_url')

    assert short_url.name == 'Short URLs'
    assert short_url.url_name == 'short_url:dashboard'
    assert short_url.icon == 'fas fa-link'
    assert reverse(short_url.url_name) == '/links/'


# --- a user's own order and switched-off apps (issue #246) ---

ALL = ['qr_code', 'file_transfer', 'receipts', 'short_url', 'todo', 'notes', 'world_time']


def _user(**attrs):
    """A stand-in for a signed-in user: `core` reads the two lists as plain attributes."""
    return SimpleNamespace(is_authenticated=True, **attrs)


def _labels(rows):
    return [(product.label, visible) for product, visible in rows]


def test_products_for_anonymous_is_everything_in_registry_order():
    rows = products_for(AnonymousUser())

    assert _labels(rows) == [(label, True) for label in ALL]


def test_products_for_ignores_a_preference_on_an_unauthenticated_object():
    user = SimpleNamespace(is_authenticated=False, app_order=['todo'], hidden_apps=['qr_code'])

    assert _labels(products_for(user)) == [(label, True) for label in ALL]


def test_products_for_an_object_without_the_attributes_is_the_default():
    assert _labels(products_for(_user())) == [(label, True) for label in ALL]
    assert _labels(products_for(None)) == [(label, True) for label in ALL]


def test_products_for_follows_the_users_order_and_hidden_apps():
    user = _user(app_order=['todo', 'receipts', 'qr_code', 'file_transfer', 'short_url'])
    user.hidden_apps = ['receipts', 'short_url']

    assert _labels(products_for(user)) == [
        ('todo', True),
        ('receipts', False),
        ('qr_code', True),
        ('file_transfer', True),
        ('short_url', False),
        ('notes', True),
        ('world_time', True),
    ]
    assert [p.label for p in visible_products(user)] == [
        'todo',
        'qr_code',
        'file_transfer',
        'notes',
        'world_time',
    ]


def test_products_for_skips_unknown_labels_and_repeats():
    user = _user(app_order=['gone', 'todo', 'todo', 'qr_code'], hidden_apps=['gone', 'receipts'])

    assert _labels(products_for(user)) == [
        ('todo', True),
        ('qr_code', True),
        ('file_transfer', True),
        ('receipts', False),
        ('short_url', True),
        ('notes', True),
        ('world_time', True),
    ]


def test_a_product_missing_from_the_saved_order_goes_last_and_is_visible():
    """A product added after the user saved their order."""
    user = _user(app_order=['todo', 'qr_code'], hidden_apps=[])

    assert [label for label, _ in _labels(products_for(user))] == [
        'todo',
        'qr_code',
        'file_transfer',
        'receipts',
        'short_url',
        'notes',
        'world_time',
    ]
    assert all(visible for _, visible in _labels(products_for(user)))


@pytest.mark.parametrize('junk', [None, 'todo', 5, {'todo': 1}, [1, None, 'todo']])
def test_products_for_survives_junk_in_the_lists(junk):
    user = _user(app_order=junk, hidden_apps=junk)

    rows = products_for(user)

    assert {product.label for product, _ in rows} == set(ALL)


def test_the_context_processor_returns_only_the_visible_products():
    request = RequestFactory().get('/')
    request.user = _user(app_order=['todo'], hidden_apps=['qr_code', 'receipts'])

    assert [p.label for p in products(request)['products']] == [
        'todo',
        'file_transfer',
        'short_url',
        'notes',
        'world_time',
    ]


def test_the_context_processor_without_a_user_returns_everything():
    request = RequestFactory().get('/')

    assert [p.label for p in products(request)['products']] == ALL


def test_header_and_home_page_follow_the_users_choice(client, user):
    user.app_order = ['todo', 'qr_code', 'file_transfer', 'receipts', 'short_url']
    user.hidden_apps = ['file_transfer']
    user.save()
    client.force_login(user)

    content = client.get(reverse('core:home')).content.decode()

    assert f'href="{reverse("file_transfer:dashboard")}"' not in content
    todo = content.index(f'href="{reverse("todo:today")}"')
    qr = content.index(f'href="{reverse("qr_code:dashboard")}"')
    assert todo < qr
    # Once in the header, once on the landing page card.
    assert content.count(f'href="{reverse("todo:today")}"') == 2
    assert 'id="product-nav"' in content
    assert 'id="no-apps"' not in content


def test_home_page_says_so_when_every_app_is_off(client, user):
    user.hidden_apps = ALL
    user.save()
    client.force_login(user)

    content = client.get(reverse('core:home')).content.decode()

    assert 'id="no-apps"' in content
    assert f'href="{reverse("accounts:account")}#apps"' in content
    for product in get_products():
        assert f'href="{reverse(product.url_name)}"' not in content


def test_an_anonymous_visitor_never_sees_the_empty_state(client):
    content = client.get(reverse('core:home')).content.decode()

    assert 'id="no-apps"' not in content


def test_a_hidden_app_still_opens_by_direct_link(client, user):
    user.hidden_apps = ['todo']
    user.save()
    client.force_login(user)

    assert client.get(reverse('todo:today')).status_code == 200
