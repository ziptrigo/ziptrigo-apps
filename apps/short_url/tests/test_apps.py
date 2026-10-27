from django.apps import apps
from django.urls import resolve, reverse

from apps.core.products import get_products


def test_the_product_is_registered():
    product = next(p for p in get_products() if p.label == 'short_url')
    assert product.name == 'Short URLs'
    assert product.icon == 'fas fa-link'
    assert product.url_name == 'short_url:dashboard'
    assert reverse(product.url_name) == '/links/'


def test_the_app_label():
    config = apps.get_app_config('short_url')
    assert config.name == 'apps.short_url'


def test_the_redirect_lives_at_the_site_root():
    assert reverse('s:redirect', args=['abc']) == '/s/abc'
    match = resolve('/s/Abc-1_x+')
    assert match.view_name == 's:redirect'
    assert match.kwargs == {'code': 'Abc-1_x', 'preview': '+'}
    assert resolve('/s/abc/').kwargs == {'code': 'abc'}


def test_the_qr_apps_links_are_untouched():
    assert resolve('/go/abc12345').view_name == 'go:redirect'
