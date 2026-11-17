"""The per-app actions area of the account page (`apps.core.app_actions`, `{% app_actions %}`)."""

import logging
from types import SimpleNamespace

import pytest
from django.template import Context, Template
from django.test import RequestFactory

from apps.core.app_actions import AppActions
from apps.core.products import ProductApp, get_products, register

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

TEMPLATE = Template('{% load app_actions %}{% app_actions %}')


def render_for(user) -> str:
    request = RequestFactory().get('/account/')
    request.user = user
    return TEMPLATE.render(Context({'request': request}))


def member(**attrs):
    return SimpleNamespace(
        **{'is_authenticated': True, 'app_order': [], 'hidden_apps': [], **attrs}
    )


@pytest.fixture
def only_products():
    """Run with the given products registered and nothing else, then restore the registry."""
    from apps.core import products

    saved = dict(products._registry)

    def install(*items: ProductApp) -> None:
        products._registry.clear()
        for item in items:
            register(item)

    yield install
    products._registry.clear()
    products._registry.update(saved)


def product(label: str, actions=None) -> ProductApp:
    return ProductApp(
        label=label,
        name=label.title(),
        description='',
        url_name='core:home',
        icon='fas fa-star',
        actions=actions,
    )


def inline(text: str, scripts: tuple[str, ...] = ()):
    return lambda request: AppActions(
        template='core/tests/_actions.html', context={'text': text}, scripts=scripts
    )


@pytest.fixture(autouse=True)
def _template(settings, tmp_path):
    folder = tmp_path / 'core' / 'tests'
    folder.mkdir(parents=True)
    (folder / '_actions.html').write_text('<p class="mine">{{ text }}</p>')
    settings.TEMPLATES = [
        {**settings.TEMPLATES[0], 'DIRS': [tmp_path, *settings.TEMPLATES[0]['DIRS']]}
    ]


def test_blocks_are_in_the_users_order_switched_off_apps_included(only_products):
    only_products(
        product('a', inline('from A')),
        product('b', inline('from B')),
        product('c'),  # no provider
    )
    user = member(app_order=['b', 'a', 'c'], hidden_apps=['b'])

    html = render_for(user)

    assert html.index('from B') < html.index('from A')
    assert 'data-app-actions="c"' not in html
    assert 'App actions' in html


def test_nothing_is_rendered_when_no_product_has_actions(only_products):
    only_products(product('a'), product('b', lambda request: None))

    assert render_for(member()).strip() == ''


def test_nothing_for_an_anonymous_visitor(only_products):
    only_products(product('a', inline('x')))

    assert render_for(SimpleNamespace(is_authenticated=False)).strip() == ''


def test_a_failing_provider_says_so_and_is_logged(only_products, caplog):
    def broken(request):
        raise RuntimeError('boom')

    only_products(product('a', broken), product('b', inline('still here')))

    with caplog.at_level(logging.ERROR):
        html = render_for(member())

    assert 'Not available right now.' in html
    assert 'still here' in html
    assert 'Actions of a failed' in caplog.text


def test_a_provider_is_called_once_per_render_with_the_request(only_products):
    seen = []

    def provider(request):
        seen.append(request.user)
        return AppActions(template='core/tests/_actions.html', context={'text': 'x'})

    only_products(product('a', provider))
    user = member()
    render_for(user)

    assert seen == [user]


def test_the_installed_registry_has_receipts_and_todo_actions():
    assert any(p.label == 'receipts' and p.actions for p in get_products())
    assert any(p.label == 'todo' and p.actions for p in get_products())


def test_scripts_are_loaded_once_each_before_the_blocks(only_products):
    only_products(
        product('a', inline('from A', scripts=('a/one.js', 'shared.js'))),
        product('b', inline('from B', scripts=('shared.js', 'b/two.js'))),
    )

    html = render_for(member())

    assert html.count('shared.js') == 1
    assert html.index('a/one.js') < html.index('shared.js') < html.index('b/two.js')
    assert html.index('b/two.js') < html.index('from A')
    assert '<script src="/static/a/one.js"></script>' in html


def test_a_failing_provider_brings_no_scripts(only_products):
    def broken(request):
        raise RuntimeError('boom')

    only_products(product('a', broken), product('b', inline('ok', scripts=('b.js',))))

    html = render_for(member())

    assert '<script' in html and html.count('<script') == 1
