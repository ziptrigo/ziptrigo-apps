"""The ProductApp registry and the nav/landing page built from it."""

import pytest
from django.urls import reverse

from apps.core.products import ProductApp, get_products, register

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_installed_products_are_registered_in_installed_apps_order():
    labels = [product.label for product in get_products()]

    assert labels == ['qr_code', 'file_transfer']


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
