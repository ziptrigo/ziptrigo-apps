"""The account page's "Apps" section (issue #246): which apps a user sees, and in what order."""

import re

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services.apps import set_app_preferences
from apps.core.products import get_products, products_for

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}
LABELS = ['qr_code', 'file_transfer', 'receipts', 'short_url', 'todo']


def _post(client, order, enabled, **extra):
    return client.post(
        reverse('accounts:apps-update'), {'order': order, 'enabled': enabled}, **extra
    )


def _tiles(content: str) -> list[tuple[str, bool]]:
    """(label, checked) of each tile of the Apps form, in page order."""
    form = re.search(r'<form id="apps-form".*?</form>', content, flags=re.DOTALL)
    assert form is not None
    inputs = re.findall(r'<input type="checkbox"[^>]*>', form.group(0))
    return [(re.search(r'value="(.*?)"', tag).group(1), 'checked' in tag) for tag in inputs]  # ty: ignore[unresolved-attribute]


def _order_inputs(content: str) -> list[str]:
    form = re.search(r'<form id="apps-form".*?</form>', content, flags=re.DOTALL)
    assert form is not None
    return re.findall(r'<input type="hidden" name="order" value="(.*?)"', form.group(0))


# --- the model and the service ---


def test_a_new_user_has_no_preferences(user):
    user.refresh_from_db()
    assert user.app_order == []
    assert user.hidden_apps == []


def test_columns_have_database_defaults():
    """So a container of the previous release, which doesn't know the columns, can still insert
    users while the migration runs."""
    for name in ('app_order', 'hidden_apps'):
        assert User._meta.get_field(name).db_default == []  # ty: ignore[unresolved-attribute]


def test_set_app_preferences_saves_order_and_hidden_in_order(user):
    set_app_preferences(user, ['todo', 'receipts', 'qr_code'], ['qr_code', 'todo'])

    user.refresh_from_db()
    # The stored order always lists every product; hidden apps keep their place.
    assert user.app_order == ['todo', 'receipts', 'qr_code', 'file_transfer', 'short_url']
    assert user.hidden_apps == ['todo', 'qr_code']


def test_set_app_preferences_drops_repeats(user):
    set_app_preferences(user, ['todo', 'todo', 'qr_code'], [])

    user.refresh_from_db()
    assert user.app_order[:2] == ['todo', 'qr_code']
    assert len(user.app_order) == len(LABELS)


def test_set_app_preferences_bumps_updated_at(user):
    before = User.objects.get(pk=user.pk).updated_at

    set_app_preferences(user, LABELS, [])

    assert User.objects.get(pk=user.pk).updated_at > before


@pytest.mark.parametrize(
    ('order', 'hidden'), [(['nope'], []), (LABELS, ['nope']), (['todo', '<script>'], [])]
)
def test_set_app_preferences_rejects_an_unknown_label(user, order, hidden):
    with pytest.raises(ValueError):
        set_app_preferences(user, order, hidden)

    user.refresh_from_db()
    assert user.app_order == []
    assert user.hidden_apps == []


# --- the account page ---


def test_account_page_shows_the_apps_section_with_every_app_on(client, user):
    client.force_login(user)

    response = client.get(reverse('accounts:account'))

    content = response.content.decode()
    assert response.status_code == 200
    assert 'id="apps"' in content
    assert f'hx-post="{reverse("accounts:apps-update")}"' in content
    assert 'Choose the apps you use.' in content
    assert _tiles(content) == [(label, True) for label in LABELS]
    assert _order_inputs(content) == LABELS


def test_account_page_follows_the_saved_order_and_unchecks_hidden_apps(client, user):
    set_app_preferences(user, ['todo', 'qr_code'], ['qr_code'])
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert _tiles(content) == [
        ('todo', True),
        ('qr_code', False),
        ('file_transfer', True),
        ('receipts', True),
        ('short_url', True),
    ]


def test_tiles_show_the_icon_and_name_only(client, user):
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()
    form = re.search(r'<form id="apps-form".*?</form>', content, flags=re.DOTALL)
    assert form is not None

    for product in get_products():
        assert product.icon in form.group(0)
        assert product.name in form.group(0)
        assert product.description not in form.group(0)


def test_form_is_one_request_at_a_time_and_has_a_noscript_save(client, user):
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()
    form = re.search(r'<form id="apps-form".*?>', content, flags=re.DOTALL)

    assert form is not None
    assert 'hx-sync="this:queue last"' in form.group(0)
    assert 'hx-trigger="change"' in form.group(0)
    assert re.search(r'<noscript>\s*<button type="submit"', content)


def test_account_page_loads_the_ordering_script(client, user):
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert 'accounts/js/apps_order.js' in content
    assert 'Sortable.min.js' in content


# --- saving ---


def test_htmx_save_stores_the_choice_and_answers_the_nav_out_of_band(client, user):
    client.force_login(user)

    order = ['todo', 'receipts', 'qr_code', 'file_transfer', 'short_url']
    response = _post(client, order, ['todo', 'qr_code'], **HTMX)

    assert response.status_code == 200
    # The tiles stay where they are, so the keyboard focus isn't lost.
    assert response['HX-Reswap'] == 'none'
    content = response.content.decode()
    assert '<html' not in content
    assert 'id="product-nav"' in content
    assert 'hx-swap-oob="true"' in content
    # Only the apps left on, in their order.
    assert content.index(reverse('todo:today')) < content.index(reverse('qr_code:dashboard'))
    assert reverse('receipts:dashboard') not in content
    assert reverse('short_url:dashboard') not in content
    assert reverse('file_transfer:dashboard') not in content
    user.refresh_from_db()
    assert user.hidden_apps == ['receipts', 'file_transfer', 'short_url']
    assert user.app_order == ['todo', 'receipts', 'qr_code', 'file_transfer', 'short_url']


def test_without_htmx_saves_and_redirects_to_the_account_page(client, user):
    client.force_login(user)

    response = _post(client, LABELS, ['qr_code'])

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:account')
    user.refresh_from_db()
    assert user.hidden_apps == ['file_transfer', 'receipts', 'short_url', 'todo']


def test_unchecking_everything_hides_every_app(client, user):
    client.force_login(user)

    response = client.post(reverse('accounts:apps-update'), {'order': LABELS}, **HTMX)

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.hidden_apps == LABELS
    assert [product.label for product, visible in products_for(user) if visible] == []


def test_switching_an_app_back_on_puts_it_where_it_was(client, user):
    client.force_login(user)
    _post(client, ['todo', 'qr_code'], ['qr_code'])  # todo off, but first in the order

    _post(
        client, ['todo', 'qr_code'], ['qr_code', 'todo', 'file_transfer', 'receipts', 'short_url']
    )

    user.refresh_from_db()
    assert user.hidden_apps == []
    assert user.app_order[:2] == ['todo', 'qr_code']


def test_a_product_the_form_did_not_list_is_not_switched_off(client, user):
    """A page rendered before an app was added must not hide it by leaving it out."""
    client.force_login(user)

    _post(client, ['todo', 'qr_code'], ['todo'])

    user.refresh_from_db()
    assert user.hidden_apps == ['qr_code']
    assert 'receipts' not in user.hidden_apps


@pytest.mark.parametrize('field', ['order', 'enabled'])
def test_unknown_label_is_a_422_and_saves_nothing(client, user, field):
    set_app_preferences(user, ['todo'], ['qr_code'])
    client.force_login(user)
    data = {'order': LABELS, 'enabled': LABELS}
    data[field] = LABELS + ['<script>alert(1)</script>']

    response = client.post(reverse('accounts:apps-update'), data, **HTMX)

    assert response.status_code == 422
    # An error is swapped in over the form (no `HX-Reswap`), so the message shows.
    assert 'HX-Reswap' not in response
    content = response.content.decode()
    assert '<html' not in content
    assert 'Unknown app' in content
    assert '<script>alert' not in content
    # The saved state is what the form shows.
    assert _tiles(content)[0] == ('todo', True)
    assert ('qr_code', False) in _tiles(content)
    user.refresh_from_db()
    assert user.app_order[0] == 'todo'
    assert user.hidden_apps == ['qr_code']


def test_unknown_label_without_htmx_renders_the_page_as_422(client, user):
    client.force_login(user)

    response = client.post(reverse('accounts:apps-update'), {'order': ['nope']})

    assert response.status_code == 422
    assert '<html' in response.content.decode()
    user.refresh_from_db()
    assert user.app_order == []


def test_get_is_not_allowed(client, user):
    client.force_login(user)

    assert client.get(reverse('accounts:apps-update')).status_code == 405


def test_anonymous_post_is_sent_to_login_and_saves_nothing(client, user):
    response = _post(client, LABELS, [])

    assert response.status_code == 302
    assert reverse('accounts:login') in response['Location']
    user.refresh_from_db()
    assert user.hidden_apps == []


def test_anonymous_htmx_post_redirects_to_login(client):
    response = _post(client, LABELS, [], **HTMX)

    assert response.status_code == 200
    assert reverse('accounts:login') in response['HX-Redirect']


# --- what the rest of the site shows ---


def test_saved_choice_shapes_the_header_on_another_page(client, user):
    set_app_preferences(user, ['todo', 'qr_code'], ['qr_code', 'receipts'])
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()
    nav = re.search(r'<div id="product-nav".*?</div>', content, flags=re.DOTALL)

    assert nav is not None
    assert reverse('todo:today') in nav.group(0)
    assert reverse('qr_code:dashboard') not in nav.group(0)
    assert reverse('receipts:dashboard') not in nav.group(0)


def test_another_users_choice_does_not_apply(client, user, regular_user):
    set_app_preferences(regular_user, LABELS, LABELS)
    client.force_login(user)

    content = client.get(reverse('core:home')).content.decode()

    assert 'id="no-apps"' not in content
    assert reverse('todo:today') in content


# --- the admin ---


def test_admin_shows_the_preferences_read_only(client, admin_user, regular_user):
    set_app_preferences(regular_user, ['todo'], ['qr_code'])
    client.force_login(admin_user)

    url = reverse('custom_admin:accounts_user_change', args=[regular_user.pk])
    response = client.get(url)

    content = response.content.decode()
    assert response.status_code == 200
    assert 'name="app_order"' not in content
    assert 'name="hidden_apps"' not in content
    assert 'App order' in content
    assert 'Hidden apps' in content
    assert 'qr_code' in content
