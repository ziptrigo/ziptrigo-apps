"""The receipts JWT API's category and inbox endpoints (`/api/receipts/categories`, `.../inbox`)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from ..models import (
    DEFAULT_CATEGORIES,
    InboundEmail,
    InboundEmailStatus,
    InboxCode,
    ReceiptCategory,
    ReceiptsAccount,
)
from ..services.accounts import get_account
from .api_support import (  # noqa: F401 -- the fixtures are used by name
    auth,
    delete,
    get,
    limit_rule,
    other_auth,
    patch,
    post,
)
from .factories import create_inbound_email, create_receipt

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def names(response) -> list[str]:
    return [c['name'] for c in response.json()]


class TestCategories:
    def test_the_first_call_seeds_the_defaults_in_order(self, client, auth):
        response = get(client, '/categories', auth)

        assert response.status_code == 200
        assert names(response) == list(DEFAULT_CATEGORIES)
        first = response.json()[0]
        assert set(first) == {'id', 'name', 'sort_order', 'receipt_count'}
        assert first['sort_order'] == 0 and first['receipt_count'] == 0

    def test_only_the_callers_categories(self, client, auth, other_user):
        get_account(other_user)
        ReceiptCategory.objects.create(user=other_user, name='Secret')

        assert 'Secret' not in names(get(client, '/categories', auth))

    def test_receipt_count(self, client, auth, funded_user, fake_storage):
        category = ReceiptCategory.objects.get(user=get_account(funded_user).user, name='Travel')
        for _ in range(2):
            create_receipt(funded_user, fake_storage, category=category)

        by_name = {c['name']: c for c in get(client, '/categories', auth).json()}

        assert by_name['Travel']['receipt_count'] == 2 and by_name['Other']['receipt_count'] == 0

    def test_create_appends(self, client, auth):
        response = post(client, '/categories', auth, {'name': '  Coffee   beans '})

        assert response.status_code == 201
        body = response.json()
        assert body['name'] == 'Coffee beans' and body['receipt_count'] == 0
        assert body['sort_order'] == len(DEFAULT_CATEGORIES)
        assert names(get(client, '/categories', auth))[-1] == 'Coffee beans'

    @pytest.mark.parametrize(
        ('name', 'message'),
        [('', 'Enter a category name'), ('travel', 'already have'), ('x' * 101, 'at most')],
    )
    def test_create_rejects_a_bad_name(self, client, auth, name, message):
        response = post(client, '/categories', auth, {'name': name})

        assert response.status_code == 400 and message in response.json()['detail']

    def test_rename(self, client, auth, funded_user):
        get_account(funded_user)
        category = ReceiptCategory.objects.get(user=funded_user, name='Equipment')

        response = patch(client, f'/categories/{category.pk}', auth, {'name': 'Gear'})

        assert response.status_code == 200
        assert response.json()['name'] == 'Gear' and response.json()['id'] == category.pk
        category.refresh_from_db()
        assert category.name == 'Gear'

    def test_rename_reports_the_receipt_count(self, client, auth, funded_user, fake_storage):
        get_account(funded_user)
        category = ReceiptCategory.objects.get(user=funded_user, name='Equipment')
        create_receipt(funded_user, fake_storage, category=category)
        create_receipt(funded_user, fake_storage, category=category, deleted_at=timezone.now())

        response = patch(client, f'/categories/{category.pk}', auth, {'name': 'Gear'})

        assert response.json()['receipt_count'] == 2  # the Trash counts, like the list

    def test_a_category_deleted_in_between_is_a_404_not_a_500(self, funded_user):
        from django.http import Http404

        from ..api.categories import _counted

        get_account(funded_user)
        category = ReceiptCategory.objects.get(user=funded_user, name='Equipment')
        category.delete()

        with pytest.raises(Http404):
            _counted(funded_user, category.pk)

    def test_rename_to_an_existing_name_is_a_400(self, client, auth, funded_user):
        get_account(funded_user)
        category = ReceiptCategory.objects.get(user=funded_user, name='Equipment')

        response = patch(client, f'/categories/{category.pk}', auth, {'name': 'Travel'})

        assert response.status_code == 400

    def test_delete_uncategorizes_the_receipts(self, client, auth, funded_user, fake_storage):
        get_account(funded_user)
        category = ReceiptCategory.objects.get(user=funded_user, name='Travel')
        receipt = create_receipt(funded_user, fake_storage, category=category)

        response = delete(client, f'/categories/{category.pk}', auth)

        assert response.status_code == 204
        assert not ReceiptCategory.objects.filter(pk=category.pk).exists()
        receipt.refresh_from_db()
        assert receipt.category_id is None

    def test_another_users_category_is_a_404(self, client, auth, other_user):
        get_account(other_user)
        theirs = ReceiptCategory.objects.filter(user=other_user).first()

        assert patch(client, f'/categories/{theirs.pk}', auth, {'name': 'x'}).status_code == 404
        assert delete(client, f'/categories/{theirs.pk}', auth).status_code == 404
        assert ReceiptCategory.objects.filter(pk=theirs.pk).exists()

    def test_reorder(self, client, auth, funded_user):
        get_account(funded_user)
        mine = list(ReceiptCategory.objects.filter(user=funded_user))
        wanted = [mine[2].pk, mine[0].pk]

        response = post(client, '/categories/reorder', auth, {'ids': wanted})

        assert response.status_code == 200
        order = [c['id'] for c in response.json()]
        # listed ones first, the others after in their old order
        assert order[:2] == wanted
        assert order[2:] == [c.pk for c in mine if c.pk not in wanted]
        assert [c['sort_order'] for c in response.json()] == list(range(len(mine)))

    def test_reorder_with_someone_elses_id_is_a_400(self, client, auth, funded_user, other_user):
        get_account(funded_user)
        get_account(other_user)
        theirs = ReceiptCategory.objects.filter(user=other_user).first()
        before = names(get(client, '/categories', auth))

        response = post(client, '/categories/reorder', auth, {'ids': [theirs.pk]})

        assert response.status_code == 400
        assert names(get(client, '/categories', auth)) == before


class TestInbox:
    def test_the_address_allowlist_and_currency(self, client, auth, funded_user):
        response = get(client, '/inbox', auth)

        assert response.status_code == 200
        body = response.json()
        code = InboxCode.objects.get(account__user=funded_user, active=True).code
        assert body == {
            'address': f'receipts+{code}@ziptrigo.com',
            'sender_allowlist': [],
            'default_currency': 'USD',
            'forwarding_confirmation': None,
        }

    def test_update_the_allowlist_and_currency(self, client, auth, funded_user):
        response = patch(
            client,
            '/inbox',
            auth,
            {'sender_allowlist': ['Me@Example.com', '*@shop.example'], 'default_currency': 'eur'},
        )

        assert response.status_code == 200
        body = response.json()
        assert body['sender_allowlist'] == ['me@example.com', '*@shop.example']
        assert body['default_currency'] == 'EUR'
        account = ReceiptsAccount.objects.get(pk=funded_user.pk)
        assert account.sender_allowlist == ['me@example.com', '*@shop.example']

    def test_only_the_fields_present_are_applied(self, client, auth, funded_user):
        patch(client, '/inbox', auth, {'sender_allowlist': ['a@b.co'], 'default_currency': 'EUR'})

        only_currency = patch(client, '/inbox', auth, {'default_currency': 'GBP'}).json()
        assert only_currency['sender_allowlist'] == ['a@b.co']
        assert only_currency['default_currency'] == 'GBP'

        cleared = patch(client, '/inbox', auth, {'sender_allowlist': []}).json()
        assert cleared['sender_allowlist'] == [] and cleared['default_currency'] == 'GBP'

        assert patch(client, '/inbox', auth, {}).json()['default_currency'] == 'GBP'

    @pytest.mark.parametrize(
        ('payload', 'field'),
        [
            ({'sender_allowlist': ['not an address']}, 'sender_allowlist'),
            ({'default_currency': 'dollars'}, 'default_currency'),
        ],
    )
    def test_a_bad_value_is_a_400_and_changes_nothing(self, client, auth, payload, field):
        before = get(client, '/inbox', auth).json()

        response = patch(client, '/inbox', auth, payload)

        assert response.status_code == 400 and response.json()['detail'].startswith(field)
        assert get(client, '/inbox', auth).json() == before

    @pytest.mark.parametrize(
        'payload',
        [
            {'sender': ['a@b.co']},
            {'default_currency': 'EUR', 'address': 'x@y.z'},
            {'currency': 'EUR'},
        ],
    )
    def test_an_unknown_key_is_a_422_and_nothing_is_applied(self, client, auth, payload):
        before = get(client, '/inbox', auth).json()

        response = patch(client, '/inbox', auth, payload)

        assert response.status_code == 422 and isinstance(response.json()['detail'], list)
        assert get(client, '/inbox', auth).json() == before

    def test_null_leaves_a_setting_unchanged(self, client, auth):
        # The opposite of `PATCH /receipts/{id}`, where null clears the field.
        patch(client, '/inbox', auth, {'sender_allowlist': ['a@b.co'], 'default_currency': 'EUR'})

        body = patch(
            client, '/inbox', auth, {'sender_allowlist': None, 'default_currency': None}
        ).json()

        assert body['sender_allowlist'] == ['a@b.co'] and body['default_currency'] == 'EUR'

    def test_regenerate_retires_the_old_address(self, client, auth, funded_user):
        old = get(client, '/inbox', auth).json()['address']

        response = post(client, '/inbox/regenerate', auth)

        assert response.status_code == 200
        new = response.json()['address']
        assert new != old and new.startswith('receipts+') and new.endswith('@ziptrigo.com')
        assert get(client, '/inbox', auth).json()['address'] == new
        old_code = old.split('+', 1)[1].split('@')[0]
        assert InboxCode.objects.get(code=old_code).active is False

    def test_regenerate_is_rate_limited(self, client, auth, settings):
        limit_rule(settings, 'RECEIPTS_REGENERATE_USER')

        assert post(client, '/inbox/regenerate', auth).status_code == 200
        limited = post(client, '/inbox/regenerate', auth)

        assert limited.status_code == 429 and limited['Retry-After']

    def test_the_budget_is_shared_with_the_web_page(self, client, auth, web, settings):
        limit_rule(settings, 'RECEIPTS_REGENERATE_USER')

        assert web.post('/receipts/settings/regenerate/').status_code in (200, 302)
        assert post(client, '/inbox/regenerate', auth).status_code == 429

    def test_a_recent_forwarding_confirmation_is_shown(self, client, auth, funded_user):
        get_account(funded_user)
        create_inbound_email(
            funded_user,
            status=InboundEmailStatus.FORWARDING_CONFIRMATION,
            forwarding_confirmation_code='123456789',
            forwarding_confirmation_url='https://mail-settings.google.com/mail/vf-abc',
        )

        confirmation = get(client, '/inbox', auth).json()['forwarding_confirmation']

        assert confirmation['code'] == '123456789'
        assert confirmation['url'] == 'https://mail-settings.google.com/mail/vf-abc'
        assert confirmation['received_at']

    def test_an_unsafe_link_is_withheld_and_an_old_one_is_gone(self, client, auth, funded_user):
        get_account(funded_user)
        create_inbound_email(
            funded_user,
            status=InboundEmailStatus.FORWARDING_CONFIRMATION,
            forwarding_confirmation_code='111111',
            forwarding_confirmation_url='https://evil.example/mail-settings',
        )

        confirmation = get(client, '/inbox', auth).json()['forwarding_confirmation']
        assert confirmation['code'] == '111111' and confirmation['url'] == ''

        InboundEmail.objects.update(created_at=timezone.now() - timedelta(days=8))
        assert get(client, '/inbox', auth).json()['forwarding_confirmation'] is None

    @pytest.mark.parametrize(
        'link',
        [
            'https://evil.example\\@mail-settings.google.com/mail/vf',
            'https://evil.example@mail-settings.google.com/mail/vf',
            'https://mail-settings.google.com:8443/mail/vf',
        ],
    )
    def test_a_link_with_a_tricky_authority_is_withheld(self, client, auth, funded_user, link):
        get_account(funded_user)
        create_inbound_email(
            funded_user,
            status=InboundEmailStatus.FORWARDING_CONFIRMATION,
            forwarding_confirmation_code='111111',
            forwarding_confirmation_url=link,
        )

        confirmation = get(client, '/inbox', auth).json()['forwarding_confirmation']

        assert confirmation['code'] == '111111' and confirmation['url'] == ''

    def test_another_users_confirmation_is_not_shown(self, client, auth, other_user):
        get_account(other_user)
        create_inbound_email(
            other_user,
            status=InboundEmailStatus.FORWARDING_CONFIRMATION,
            forwarding_confirmation_code='999999',
        )

        assert get(client, '/inbox', auth).json()['forwarding_confirmation'] is None
