"""The settings page: address, accepted senders, currency, categories, recent emails
(`views/account_settings.py`)."""

from datetime import timedelta

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from ..models import (
    DEFAULT_CATEGORIES,
    InboundEmail,
    InboxCode,
    Receipt,
    ReceiptCategory,
    ReceiptStatus,
)
from ..services.accounts import get_account, inbox_address_for
from .factories import create_inbound_email, create_receipt

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HX = {'HTTP_HX_REQUEST': 'true'}


def flashed(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


class TestPage:
    def test_shows_the_address_the_forms_and_the_default_categories(self, web, funded_user):
        response = web.get(reverse('receipts:settings'))
        content = response.content.decode()

        assert response.status_code == 200
        assert inbox_address_for(funded_user) in content
        assert 'Copy' in content and 'Regenerate' in content
        assert 'Your old address stops working immediately' in content
        for name in DEFAULT_CATEGORIES:
            assert name.replace('&', '&amp;') in content
        assert 'Anyone' in content and 'Only these' in content
        assert 'Default currency' in content
        assert 'No emails yet.' in content

    def test_the_stored_allowlist_and_currency_are_shown(self, web, funded_user):
        account = get_account(funded_user)
        account.sender_allowlist = ['me@acme.com', '*@acme.com']
        account.default_currency = 'EUR'
        account.save()

        content = web.get(reverse('receipts:settings')).content.decode()

        assert 'me@acme.com, *@acme.com</textarea>' in content
        assert '<option value="EUR" selected>' in content
        assert 'value="only"' in content
        # The mode reaches Alpine through a data attribute, never interpolated into JS.
        assert 'data-mode="only"' in content and 'x-data="{ mode: $el.dataset.mode }"' in content

    def test_a_forwarding_confirmation_shows_its_code_and_link(self, web, funded_user):
        create_inbound_email(
            funded_user,
            status='forwarding_confirmation',
            forwarding_confirmation_code='482913',
            forwarding_confirmation_url='https://mail-settings.google.com/mail/vf-abc',
        )

        content = web.get(reverse('receipts:settings')).content.decode()

        assert 'Gmail forwarding confirmation received' in content and '482913' in content
        assert 'href="https://mail-settings.google.com/mail/vf-abc"' in content
        assert 'rel="noopener noreferrer"' in content

    @pytest.mark.parametrize(
        'link',
        [
            'https://evil.example/phish',
            'http://mail-settings.google.com/mail/vf',
            'javascript:alert(1)',
            'https://mail-settings.google.com.evil.example/x',
            'https://[x',
            'https://evil.example\\@mail-settings.google.com/mail/vf',
            'https://evil.example@mail-settings.google.com/mail/vf',
            'https://mail-settings.google.com:8443/mail/vf',
            'https://mail-settings.google.com/mail/\x01vf',
        ],
    )
    def test_a_link_that_is_not_googles_is_never_rendered(self, web, funded_user, link):
        create_inbound_email(
            funded_user,
            status='forwarding_confirmation',
            forwarding_confirmation_code='482913',
            forwarding_confirmation_url=link,
        )

        content = web.get(reverse('receipts:settings')).content.decode()

        assert '482913' in content  # the code still helps
        assert 'evil.example' not in content and 'javascript:' not in content
        assert '>Open<' not in content.replace(' ', '').replace('\n', '')

    def test_an_old_confirmation_is_not_shown(self, web, funded_user):
        email = create_inbound_email(
            funded_user, status='forwarding_confirmation', forwarding_confirmation_code='482913'
        )
        InboundEmail.objects.filter(pk=email.pk).update(
            created_at=timezone.now() - timedelta(days=8)
        )

        assert (
            'Gmail forwarding confirmation'
            not in web.get(reverse('receipts:settings')).content.decode()
        )

    def test_recent_emails_with_their_outcomes(self, web, funded_user, fake_storage):
        accepted = create_inbound_email(
            funded_user, from_address='orders@staples.com', subject='Your receipt'
        )
        create_receipt(
            funded_user, fake_storage, inbound_email=accepted, status=ReceiptStatus.PROCESSED
        )
        create_inbound_email(
            funded_user, from_address='promo@shop.com', subject='50% off!', status='rejected_sender'
        )
        create_inbound_email(
            funded_user,
            from_address='unknown@spam.biz',
            subject='Invoice',
            status='rejected_spam',
        )
        create_inbound_email(
            funded_user,
            from_address='big@vendor.com',
            status='rejected_cap',
            note='daily limit reached after 100 files',
        )
        create_inbound_email(funded_user, from_address='x@y.com', status='no_receipt')

        content = web.get(reverse('receipts:settings')).content.decode()

        assert 'orders@staples.com' in content and '1 receipt' in content
        assert 'sender not allowed' in content and 'flagged as spam' in content
        assert 'daily limit reached' in content and 'daily limit reached after 100 files' in content
        assert 'no receipt found' in content

    def test_what_a_sender_wrote_is_escaped(self, web, funded_user):
        create_inbound_email(
            funded_user,
            from_address='"><script>alert(1)</script>@evil.test',
            subject='<img src=x onerror=alert(2)>',
        )

        content = web.get(reverse('receipts:settings')).content.decode()

        assert '<script>alert' not in content and '<img src=x' not in content

    def test_only_the_owners_emails_are_listed(self, web, other_user):
        create_inbound_email(other_user, from_address='secret@theirs.test')

        assert 'secret@theirs.test' not in web.get(reverse('receipts:settings')).content.decode()

    def test_only_the_20_most_recent_emails(self, web, funded_user):
        for index in range(25):
            create_inbound_email(funded_user, from_address=f'sender{index:02d}@x.test')

        content = web.get(reverse('receipts:settings')).content.decode()

        assert content.count('@x.test') == 20


class TestRegenerate:
    def test_issues_a_new_address_and_retires_the_old_one(self, web, funded_user):
        old = inbox_address_for(funded_user)

        response = web.post(reverse('receipts:regenerate'))

        assert response.status_code == 302 and response['Location'] == reverse('receipts:settings')
        new = inbox_address_for(funded_user)
        assert new != old
        assert InboxCode.objects.filter(account__user=funded_user, active=True).count() == 1
        assert InboxCode.objects.filter(account__user=funded_user, active=False).count() == 1
        assert 'old one no longer works' in flashed(response)[0]

    def test_it_needs_post(self, web):
        assert web.get(reverse('receipts:regenerate')).status_code == 405

    def test_it_is_rate_limited(self, web, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'RECEIPTS_REGENERATE_USER': (1, 3600),
        }

        assert web.post(reverse('receipts:regenerate')).status_code == 302
        limited = web.post(reverse('receipts:regenerate'))

        assert limited.status_code == 429
        assert InboxCode.objects.filter(account__user__isnull=False, active=False).count() == 1

    def test_requires_login(self, client):
        assert client.post(reverse('receipts:regenerate')).status_code == 302


class TestInboxSettings:
    def post(self, web, **fields):
        data = {'sender_mode': 'any', 'sender_allowlist': '', 'default_currency': 'USD'}
        data.update(fields)
        return web.post(reverse('receipts:settings-inbox'), data, **HX)

    def test_saves_the_allowlist_and_currency_and_says_so(self, web, funded_user):
        response = self.post(
            web,
            sender_mode='only',
            sender_allowlist='Me+tag@Acme.com, *@acme.com\nme@acme.com',
            default_currency='eur',
        )

        assert response.status_code == 200
        assert 'Saved' in response.content.decode()
        account = get_account(funded_user)
        assert account.sender_allowlist == ['me@acme.com', '*@acme.com']
        assert account.default_currency == 'EUR'

    def test_anyone_clears_the_allowlist(self, web, funded_user):
        self.post(web, sender_mode='only', sender_allowlist='me@acme.com')

        self.post(web, sender_mode='any', sender_allowlist='me@acme.com')

        assert get_account(funded_user).sender_allowlist == []

    @pytest.mark.parametrize(
        ('fields', 'message'),
        [
            ({'sender_mode': 'only', 'sender_allowlist': 'not an address'}, 'Use email addresses'),
            ({'sender_mode': 'only', 'sender_allowlist': ''}, 'Enter at least one address'),
            ({'default_currency': 'ZZZ'}, 'Choose a currency'),
            ({'sender_mode': 'whoever'}, 'Accepted senders'),
        ],
    )
    def test_invalid_input_is_a_422_for_the_message_slot(self, web, funded_user, fields, message):
        response = self.post(web, **fields)

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#inbox-settings-msg'
        assert message in response.content.decode()
        assert get_account(funded_user).sender_allowlist == []

    def test_without_htmx_it_flashes_and_redirects(self, web, funded_user):
        ok = web.post(
            reverse('receipts:settings-inbox'),
            {'sender_mode': 'any', 'default_currency': 'GBP'},
        )
        bad = web.post(
            reverse('receipts:settings-inbox'),
            {'sender_mode': 'only', 'sender_allowlist': 'nope', 'default_currency': 'GBP'},
        )

        assert ok.status_code == bad.status_code == 302
        assert flashed(ok) == ['Saved.'] and 'Use email addresses' in flashed(bad)[-1]
        assert get_account(funded_user).default_currency == 'GBP'


class TestCategories:
    def names(self, user):
        return list(ReceiptCategory.objects.filter(user=user).values_list('name', flat=True))

    def test_add(self, web, funded_user):
        response = web.post(reverse('receipts:category-create'), {'name': '  Gifts  '}, **HX)

        assert response.status_code == 200
        assert self.names(funded_user)[-1] == 'Gifts'
        assert 'Gifts' in response.content.decode()
        assert '<html' not in response.content.decode()

    @pytest.mark.parametrize(
        ('name', 'message'),
        [
            ('', 'Enter a category name'),
            ('travel', 'already have a category'),
            ('x' * 101, 'at most 100 characters'),
        ],
    )
    def test_add_errors(self, web, funded_user, name, message):
        get_account(funded_user)

        response = web.post(reverse('receipts:category-create'), {'name': name}, **HX)

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#category-msg'
        assert message in response.content.decode()

    def test_rename(self, web, funded_user):
        get_account(funded_user)
        category = ReceiptCategory.objects.get(user=funded_user, name='Travel')

        response = web.post(
            reverse('receipts:category-rename', args=[category.pk]), {'name': 'Trips'}, **HX
        )

        assert response.status_code == 200
        category.refresh_from_db()
        assert category.name == 'Trips'

    def test_rename_to_an_existing_name_is_a_422(self, web, funded_user):
        get_account(funded_user)
        category = ReceiptCategory.objects.get(user=funded_user, name='Travel')

        response = web.post(
            reverse('receipts:category-rename', args=[category.pk]), {'name': 'equipment'}, **HX
        )

        assert response.status_code == 422

    def test_move_up_and_down(self, web, funded_user):
        get_account(funded_user)
        second = ReceiptCategory.objects.get(user=funded_user, name=DEFAULT_CATEGORIES[1])

        web.post(reverse('receipts:category-move', args=[second.pk]), {'direction': 'up'}, **HX)
        assert self.names(funded_user)[:2] == [DEFAULT_CATEGORIES[1], DEFAULT_CATEGORIES[0]]

        web.post(reverse('receipts:category-move', args=[second.pk]), {'direction': 'down'}, **HX)
        assert self.names(funded_user)[:2] == list(DEFAULT_CATEGORIES[:2])

    def test_moving_past_either_end_changes_nothing(self, web, funded_user):
        get_account(funded_user)
        first = ReceiptCategory.objects.get(user=funded_user, name=DEFAULT_CATEGORIES[0])
        last = ReceiptCategory.objects.get(user=funded_user, name=DEFAULT_CATEGORIES[-1])

        assert (
            web.post(
                reverse('receipts:category-move', args=[first.pk]), {'direction': 'up'}, **HX
            ).status_code
            == 200
        )
        assert (
            web.post(
                reverse('receipts:category-move', args=[last.pk]), {'direction': 'down'}, **HX
            ).status_code
            == 200
        )

        assert self.names(funded_user) == list(DEFAULT_CATEGORIES)

    def test_move_needs_a_direction(self, web, funded_user):
        get_account(funded_user)
        category = ReceiptCategory.objects.filter(user=funded_user).first()

        response = web.post(
            reverse('receipts:category-move', args=[category.pk]), {'direction': 'sideways'}, **HX
        )

        assert response.status_code == 422

    def test_delete_uncategorizes_its_receipts(self, web, funded_user, make_receipt):
        get_account(funded_user)
        travel = ReceiptCategory.objects.get(user=funded_user, name='Travel')
        receipt = make_receipt(category=travel, status=ReceiptStatus.PROCESSED)

        response = web.post(reverse('receipts:category-delete', args=[travel.pk]), **HX)

        assert response.status_code == 200
        assert 'Deleted Travel. Its receipts are now uncategorized.' in response.content.decode()
        assert not ReceiptCategory.objects.filter(pk=travel.pk).exists()
        assert Receipt.objects.get(pk=receipt.pk).category_id is None

    def test_delete_asks_first_and_names_the_receipts_affected(
        self, web, funded_user, make_receipt
    ):
        get_account(funded_user)
        travel = ReceiptCategory.objects.get(user=funded_user, name='Travel')
        make_receipt(category=travel, status=ReceiptStatus.PROCESSED)
        make_receipt(category=travel, status=ReceiptStatus.PROCESSED)

        content = web.get(reverse('receipts:settings')).content.decode()

        assert 'Its 2 receipts will become uncategorized.' in content
        assert f'data-url="{reverse("receipts:category-delete", args=[travel.pk])}"' in content

    def test_without_htmx_the_actions_redirect_to_the_page(self, web, funded_user):
        get_account(funded_user)
        category = ReceiptCategory.objects.get(user=funded_user, name='Travel')

        added = web.post(reverse('receipts:category-create'), {'name': 'Gifts'})
        deleted = web.post(reverse('receipts:category-delete', args=[category.pk]))
        bad = web.post(reverse('receipts:category-create'), {'name': ''})

        assert {added.status_code, deleted.status_code, bad.status_code} == {302}
        assert 'Deleted Travel' in flashed(deleted)[-1]
        assert 'Enter a category name' in flashed(bad)[-1]

    @pytest.mark.parametrize('name', ['category-rename', 'category-move', 'category-delete'])
    def test_another_users_category_is_a_404(self, web, other_user, name):
        get_account(other_user)
        theirs = ReceiptCategory.objects.filter(user=other_user).first()

        response = web.post(
            reverse(f'receipts:{name}', args=[theirs.pk]), {'name': 'x', 'direction': 'up'}, **HX
        )

        assert response.status_code == 404
        assert ReceiptCategory.objects.filter(pk=theirs.pk, name=theirs.name).exists()

    @pytest.mark.parametrize('name', ['category-create', 'settings-inbox', 'regenerate'])
    def test_the_forms_need_post(self, web, name):
        assert web.get(reverse(f'receipts:{name}')).status_code == 405
