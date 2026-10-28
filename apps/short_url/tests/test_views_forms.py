from datetime import UTC, timedelta

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import CreditTransaction

from ..models import LinkStatus, ShortLink
from .conftest import balance
from .factories import create_link, expired

pytestmark = pytest.mark.django_db

HX = {'HTTP_HX_REQUEST': 'true'}
CREATE = 'short_url:create'


def post_create(client, **fields):
    data = {'destination': 'https://example.com/page'}
    data.update(fields)
    return client.post(reverse(CREATE), data, **HX)


def flashes(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)][-1:]


class TestCreatePage:
    def test_login_is_required(self, client):
        assert client.get(reverse(CREATE)).status_code == 302
        assert (
            client.post(reverse(CREATE), {'destination': 'https://example.com'}).status_code == 302
        )
        assert not ShortLink.objects.exists()

    def test_the_form_shows_every_field_and_the_prices(self, web):
        content = web.get(reverse(CREATE)).content.decode()
        for name in (
            'destination',
            'alias',
            'title',
            'tags',
            'expires_at',
            'max_clicks',
            'utm_source',
            'utm_medium',
            'utm_campaign',
            'utm_term',
            'utm_content',
            'password',
            'include_bots',
        ):
            assert f'name="{name}"' in content
        assert 'An alias costs 1 credit' in content
        assert 'Your link will be' in content
        assert 'https://app.example.test/s/' in content
        assert 'id="link-form-msg"' in content and 'id="cost-confirm"' in content

    def test_a_free_alias_is_announced_as_free(self, web, prices):
        prices.custom_alias_price = 0
        prices.save()
        assert 'Aliases are free' in web.get(reverse(CREATE)).content.decode()

    def test_it_warns_when_the_link_will_be_an_extra_one(self, web, funded_user):
        create_link(funded_user)
        create_link(funded_user)
        content = web.get(reverse(CREATE)).content.decode()
        assert 'used your free active links' in content

    def test_no_warning_within_the_allowance(self, web):
        assert 'used your free active links' not in web.get(reverse(CREATE)).content.decode()


class TestCreate:
    def test_it_creates_a_link_and_goes_to_its_page(self, web, funded_user):
        response = post_create(web, title='Mine', tags='a, b')
        link = ShortLink.objects.get(owner=funded_user)
        assert response.status_code == 200
        assert response['HX-Redirect'] == reverse('short_url:detail', args=[link.pk])
        assert flashes(response) == ['Link created.']
        assert (link.title, link.tags, link.destination) == (
            'Mine',
            ['a', 'b'],
            'https://example.com/page',
        )
        assert len(link.code) == 7
        assert balance(funded_user) == 10

    def test_without_htmx_it_is_a_plain_redirect(self, web, funded_user):
        response = web.post(reverse(CREATE), {'destination': 'https://example.com/x'})
        link = ShortLink.objects.get(owner=funded_user)
        assert response.status_code == 302
        assert response['Location'] == reverse('short_url:detail', args=[link.pk])

    def test_the_whole_form_is_used(self, web, funded_user):
        expires = (timezone.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        post_create(
            web,
            destination='https://example.com/p?x=1',
            title='T',
            tags='news',
            expires_at=expires,
            max_clicks='7',
            utm_source='mail',
            utm_campaign='may sale',
        )
        link = ShortLink.objects.get(owner=funded_user)
        assert (
            link.destination == 'https://example.com/p?x=1&utm_source=mail&utm_campaign=may%20sale'
        )
        assert link.max_clicks == 7
        assert link.expires_at is not None and link.expires_at > timezone.now()

    @pytest.mark.parametrize(
        ('fields', 'message'),
        [
            ({'destination': ''}, 'Destination'),
            ({'destination': 'not a url'}, 'Destination'),
            ({'destination': 'javascript:alert(1)'}, 'http://'),
            ({'destination': 'http://127.0.0.1/admin'}, 'not allowed'),
            ({'destination': 'https://app.example.test/s/abc'}, 'short links'),
            ({'alias': 'ab'}, 'Alias'),
            ({'alias': 'admin'}, 'Alias: That alias is not available.'),
            ({'tags': 'bad tag!'}, 'Tags'),
            ({'max_clicks': '0'}, 'Click limit'),
            ({'max_clicks': 'many'}, 'Click limit'),
            ({'expires_at': '2001-01-01T00:00'}, 'Expires: The expiry must be in the future.'),
            ({'expires_at': 'tomorrow'}, 'Expires'),
            ({'title': 'x' * 300}, 'Title'),
        ],
    )
    def test_a_bad_field_is_a_422_with_a_labelled_message(self, web, fields, message):
        response = post_create(web, **fields)
        assert response.status_code == 422
        assert response['HX-Retarget'] == '#link-form-msg'
        assert message in response.content.decode()
        assert not ShortLink.objects.exists()

    def test_several_problems_are_listed_together(self, web):
        response = post_create(web, destination='', tags='bad tag!', max_clicks='0')
        content = response.content.decode()
        assert 'Destination' in content and 'Tags' in content and 'Click limit' in content

    def test_a_refused_form_does_not_spend_the_rate_limit(self, web, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'SU_CREATE_USER': (1, 3600)}
        assert post_create(web, destination='').status_code == 422
        assert post_create(web, destination='').status_code == 422
        assert post_create(web).status_code == 200

    def test_the_create_rate_limit(self, web, funded_user, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'SU_CREATE_USER': (1, 3600)}
        assert post_create(web).status_code == 200
        response = post_create(web, destination='https://example.com/two')
        assert response.status_code == 429
        assert response['Retry-After']
        assert response['HX-Retarget'] == '#link-form-msg'
        assert ShortLink.objects.filter(owner=funded_user).count() == 1

    def test_the_hard_cap(self, web, funded_user):
        for _ in range(5):
            create_link(funded_user)
        response = post_create(web)
        assert response.status_code == 422
        assert 'at most 5 links' in response.content.decode()

    def test_the_title_is_fetched_in_the_background(
        self, web, funded_user, monkeypatch, django_capture_on_commit_callbacks
    ):
        from ..services import links as links_module

        queued = []
        monkeypatch.setattr(
            links_module, 'fetch_title', type('T', (), {'enqueue': staticmethod(queued.append)})
        )
        with django_capture_on_commit_callbacks(execute=True):
            post_create(web)
        link = ShortLink.objects.get(owner=funded_user)
        assert queued == [str(link.pk)]


class TestCostConfirmation:
    """Nothing is charged until the user has seen the total and confirmed it."""

    def test_an_alias_asks_before_charging(self, web, funded_user):
        response = post_create(web, alias='Sale')
        assert response.status_code == 422
        assert response['HX-Retarget'] == '#cost-confirm'
        content = response.content.decode()
        assert '1 credit' in content and 'custom alias 1' in content
        assert 'name="confirm_cost" value="1"' in content
        assert 'Your balance is 10 credits' in content
        assert not ShortLink.objects.exists()
        assert balance(funded_user) == 10

    def test_confirming_creates_and_charges(self, web, funded_user):
        response = post_create(web, alias='Sale', confirm_cost='1')
        assert response.status_code == 200
        link = ShortLink.objects.get(owner=funded_user)
        assert link.code == 'Sale' and link.is_alias
        assert balance(funded_user) == 9
        assert CreditTransaction.objects.filter(user=funded_user, source='short_url').count() == 1

    @pytest.mark.parametrize('confirm', ['', '0', '2', 'one', '-1', '1.0'])
    def test_a_wrong_confirmation_asks_again(self, web, funded_user, confirm):
        response = post_create(web, alias='Sale', confirm_cost=confirm)
        assert response.status_code == 422
        assert response['HX-Retarget'] == '#cost-confirm'
        assert not ShortLink.objects.exists()
        assert balance(funded_user) == 10

    def test_a_confirmation_for_an_older_price_asks_again(self, web, funded_user, prices):
        prices.custom_alias_price = 3
        prices.save()
        response = post_create(web, alias='Sale', confirm_cost='1')
        assert response.status_code == 422
        assert 'value="3"' in response.content.decode()
        assert balance(funded_user) == 10

    def test_the_total_includes_every_extra(self, web, funded_user):
        create_link(funded_user)
        create_link(funded_user)
        response = post_create(web, alias='Sale')
        content = response.content.decode()
        assert 'value="2"' in content
        assert 'custom alias 1' in content and 'extra active link 1' in content
        post_create(web, alias='Sale', confirm_cost='2')
        assert balance(funded_user) == 8

    def test_an_extra_link_without_an_alias_asks_too(self, web, funded_user):
        create_link(funded_user)
        create_link(funded_user)
        assert post_create(web).status_code == 422
        assert post_create(web, confirm_cost='1').status_code == 200

    def test_an_allowance_used_up_after_the_quote_asks_instead_of_charging(
        self, web, funded_user, monkeypatch
    ):
        """Quoted as free (nothing was asked), but the free slots are gone by the charge."""
        from ..services import quote_create as real_quote
        from ..views import form as form_module

        create_link(funded_user)

        def quote_then_use_the_last_slot(owner, alias, **kwargs):
            result = real_quote(owner, alias, **kwargs)
            create_link(funded_user)  # a concurrent request fills the allowance
            return result

        monkeypatch.setattr(form_module, 'quote_create', quote_then_use_the_last_slot)
        response = post_create(web)
        assert response.status_code == 422
        assert response['HX-Retarget'] == '#cost-confirm'
        assert 'value="1"' in response.content.decode()
        assert ShortLink.objects.filter(owner=funded_user).count() == 2
        assert balance(funded_user) == 10

    def test_a_free_link_needs_no_confirmation(self, web, funded_user):
        assert post_create(web).status_code == 200
        assert post_create(web, destination='https://example.com/2').status_code == 200
        assert balance(funded_user) == 10

    def test_a_free_alias_needs_no_confirmation(self, web, funded_user, prices):
        prices.custom_alias_price = 0
        prices.save()
        assert post_create(web, alias='Free').status_code == 200

    def test_without_enough_credits_it_says_so_and_links_to_top_up(self, client, user, prices):
        client.force_login(user)
        response = post_create(client, alias='Sale', confirm_cost='1')
        assert response.status_code == 422
        assert response['HX-Retarget'] == '#link-form-msg'
        content = response.content.decode()
        assert 'have enough credits' in content
        assert reverse('billing:credits-history') in content
        assert not ShortLink.objects.exists()

    def test_the_panel_offers_top_up_when_the_balance_is_short(self, client, user, prices):
        client.force_login(user)
        content = post_create(client, alias='Sale').content.decode()
        assert reverse('billing:credits-history') in content

    def test_an_alias_taken_in_the_meantime_charges_nothing(self, web, funded_user, other_user):
        create_link(other_user, code='sale')
        response = post_create(web, alias='SALE', confirm_cost='1')
        assert response.status_code == 422
        assert 'That alias is not available.' in response.content.decode()
        assert balance(funded_user) == 10


class TestEdit:
    @pytest.fixture
    def link(self, funded_user):
        return create_link(
            funded_user,
            code='Edit01',
            destination='https://example.com/old',
            title='Old title',
            tags=['one', 'two'],
            max_clicks=9,
        )

    def url(self, link):
        return reverse('short_url:edit', args=[link.pk])

    def test_the_form_is_prefilled(self, web, link):
        content = web.get(self.url(link)).content.decode()
        assert 'value="https://example.com/old"' in content
        assert 'value="Old title"' in content
        assert 'value="one, two"' in content
        assert 'value="9"' in content
        assert 'Edit01' in content
        assert 'name="alias"' not in content  # the code can't change
        assert "can't be changed" in content

    def test_saving_changes_the_link_and_goes_to_its_page(self, web, link):
        response = web.post(
            self.url(link),
            {
                'destination': 'https://example.com/new',
                'title': 'New title',
                'tags': 'x',
                'max_clicks': '',
                'expires_at': '',
            },
            **HX,
        )
        link.refresh_from_db()
        assert response['HX-Redirect'] == reverse('short_url:detail', args=[link.pk])
        assert flashes(response) == ['Link saved.']
        assert (link.destination, link.title, link.tags, link.max_clicks) == (
            'https://example.com/new',
            'New title',
            ['x'],
            None,
        )
        assert link.code == 'Edit01'

    def test_the_redirect_follows_the_new_destination(self, web, client, link):
        web.post(self.url(link), {'destination': 'https://example.org/moved'}, **HX)
        assert client.get('/s/Edit01')['Location'] == 'https://example.org/moved'

    def test_a_posted_alias_is_ignored(self, web, link):
        web.post(
            self.url(link), {'destination': 'https://example.com/new', 'alias': 'hijack'}, **HX
        )
        link.refresh_from_db()
        assert link.code == 'Edit01'
        assert not ShortLink.objects.filter(code_key='hijack').exists()

    def test_utm_is_merged_into_the_destination(self, web, link):
        web.post(
            self.url(link),
            {'destination': 'https://example.com/old', 'utm_source': 'news'},
            **HX,
        )
        link.refresh_from_db()
        assert link.destination == 'https://example.com/old?utm_source=news'

    def test_a_bad_destination_is_a_422_and_changes_nothing(self, web, link):
        response = web.post(self.url(link), {'destination': 'http://10.0.0.1/', 'title': 'X'}, **HX)
        assert response.status_code == 422
        assert 'Destination' in response.content.decode()
        link.refresh_from_db()
        assert (link.destination, link.title) == ('https://example.com/old', 'Old title')

    def test_editing_without_a_password_never_charges(self, web, funded_user, link):
        web.post(self.url(link), {'destination': 'https://example.com/new'}, **HX)
        assert balance(funded_user) == 10

    def test_an_expired_link_can_be_saved_without_touching_its_expiry(self, web, funded_user):
        link = expired(funded_user, code='Old001')
        shown = web.get(self.url(link)).content.decode()
        # Prefilled in UTC with its offset; the page's script shows it in the browser's zone.
        assert link.expires_at is not None
        value = link.expires_at.astimezone(UTC).strftime('%Y-%m-%dT%H:%MZ')
        assert f'value="{value}"' in shown
        response = web.post(
            self.url(link),
            {'destination': 'https://example.com/x', 'title': 'Still fine', 'expires_at': value},
            **HX,
        )
        assert response.status_code == 200
        link.refresh_from_db()
        assert link.title == 'Still fine'

    def test_an_expiry_with_an_offset_is_read_in_that_offset(self, web, link):
        # What the browser submits: the local time the user picked, converted to UTC.
        local = (timezone.now() + timedelta(days=2)).astimezone(timezone.get_fixed_timezone(-300))
        sent = local.replace(second=0, microsecond=0)
        response = web.post(
            self.url(link),
            {'destination': 'https://example.com/x', 'expires_at': sent.isoformat()},
            **HX,
        )
        assert response.status_code == 200
        link.refresh_from_db()
        assert link.expires_at == sent

    def test_the_browser_s_utc_value_with_milliseconds_is_accepted(self, web, link):
        sent = (timezone.now() + timedelta(days=1)).astimezone(UTC).replace(microsecond=0)
        value = sent.strftime('%Y-%m-%dT%H:%M:%S.000Z')
        response = web.post(
            self.url(link), {'destination': 'https://example.com/x', 'expires_at': value}, **HX
        )
        assert response.status_code == 200
        link.refresh_from_db()
        assert link.expires_at == sent

    def test_a_new_expiry_in_the_past_is_refused(self, web, link):
        response = web.post(
            self.url(link),
            {'destination': 'https://example.com/x', 'expires_at': '2001-01-01T00:00'},
            **HX,
        )
        assert response.status_code == 422

    def test_a_disabled_link_can_be_edited(self, web, funded_user):
        link = create_link(funded_user, status=LinkStatus.DISABLED)
        assert (
            web.post(self.url(link), {'destination': 'https://example.com/x'}, **HX).status_code
            == 200
        )

    def test_other_users_links_and_deleted_ones_are_404(self, web, other_user, funded_user):
        theirs = create_link(other_user)
        gone = create_link(funded_user, status=LinkStatus.DELETED)
        for link in (theirs, gone):
            assert web.get(self.url(link)).status_code == 404
            assert (
                web.post(self.url(link), {'destination': 'https://example.com/x'}).status_code
                == 404
            )
        theirs.refresh_from_db()
        assert theirs.destination != 'https://example.com/x'

    def test_a_taken_down_link_cannot_be_saved(self, web, funded_user):
        link = create_link(funded_user, status=LinkStatus.TAKEN_DOWN)
        response = web.post(self.url(link), {'destination': 'https://example.com/x'}, **HX)
        assert response.status_code == 422
        assert 'can no longer be changed' in response.content.decode()

    def test_the_edit_rate_limit(self, web, link, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'SU_EDIT_USER': (1, 3600)}
        assert (
            web.post(self.url(link), {'destination': 'https://example.com/1'}, **HX).status_code
            == 200
        )
        assert (
            web.post(self.url(link), {'destination': 'https://example.com/2'}, **HX).status_code
            == 429
        )

    def test_login_is_required(self, client, link):
        assert client.get(self.url(link)).status_code == 302
        assert (
            client.post(self.url(link), {'destination': 'https://example.com/x'}).status_code == 302
        )


def post_edit(client, link, **fields):
    data = {'destination': link.destination}
    data.update(fields)
    return client.post(reverse('short_url:edit', args=[link.pk]), data, **HX)


class TestPasswordOnTheForm:
    def test_the_create_form_shows_the_password_price(self, web):
        content = web.get(reverse(CREATE)).content.decode()
        assert 'A password costs 1 credit, once per link.' in content
        assert 'name="clear_password"' not in content

    def test_a_free_password_is_announced_as_free(self, web, prices):
        prices.password_price = 0
        prices.save()
        assert 'Passwords are free.' in web.get(reverse(CREATE)).content.decode()

    def test_a_password_asks_before_charging(self, web, funded_user):
        response = post_create(web, password='hunter2')
        assert response.status_code == 422
        assert response['HX-Retarget'] == '#cost-confirm'
        assert 'password 1' in response.content.decode()
        assert not ShortLink.objects.exists()
        assert balance(funded_user) == 10

    def test_confirming_creates_a_protected_link_and_charges_once(self, web, funded_user):
        from django.contrib.auth.hashers import check_password

        response = post_create(web, password='hunter2', confirm_cost='1')
        assert response.status_code == 200
        link = ShortLink.objects.get(owner=funded_user)
        assert link.password_hash and 'hunter2' not in link.password_hash
        assert check_password('hunter2', link.password_hash)
        assert link.password_paid_at is not None
        assert balance(funded_user) == 9

    def test_an_alias_and_a_password_are_one_confirmation(self, web, funded_user):
        content = post_create(web, alias='Sale', password='hunter2').content.decode()
        assert 'value="2"' in content
        assert 'custom alias 1' in content and 'password 1' in content
        post_create(web, alias='Sale', password='hunter2', confirm_cost='2')
        assert balance(funded_user) == 8

    def test_a_free_password_needs_no_confirmation(self, web, funded_user, prices):
        prices.password_price = 0
        prices.save()
        assert post_create(web, password='hunter2').status_code == 200
        assert ShortLink.objects.get(owner=funded_user).password_hash
        assert balance(funded_user) == 10

    @pytest.mark.parametrize(
        ('password', 'message'),
        [('   ', 'Password: Enter a password.'), ('x' * 129, 'Password: Use at most 128')],
    )
    def test_a_bad_password_is_a_422(self, web, password, message):
        response = post_create(web, password=password, confirm_cost='1')
        assert response.status_code == 422
        assert message in response.content.decode()
        assert not ShortLink.objects.exists()

    def test_a_password_with_spaces_is_kept_as_typed(self, web, funded_user):
        from django.contrib.auth.hashers import check_password

        post_create(web, password=' two words ', confirm_cost='1')
        link = ShortLink.objects.get(owner=funded_user)
        assert check_password(' two words ', link.password_hash)

    def test_insufficient_credits_says_so_with_a_top_up_link(self, client, user, prices):
        client.force_login(user)
        response = post_create(client, password='hunter2', confirm_cost='1')
        assert response.status_code == 422
        assert reverse('billing:credits-history') in response.content.decode()
        assert not ShortLink.objects.exists()


class TestBotOptionOnTheForm:
    def test_bots_are_filtered_by_default(self, web, funded_user):
        post_create(web)
        assert ShortLink.objects.get(owner=funded_user).filter_bots is True

    def test_ticking_the_box_counts_bots(self, web, funded_user):
        post_create(web, include_bots='on')
        assert ShortLink.objects.get(owner=funded_user).filter_bots is False

    def test_the_edit_form_reflects_and_changes_it(self, web, funded_user):
        link = create_link(funded_user, filter_bots=False)
        content = web.get(reverse('short_url:edit', args=[link.pk])).content.decode()
        assert 'name="include_bots" value="on" checked' in content
        assert post_edit(web, link).status_code == 200  # the box left unticked
        link.refresh_from_db()
        assert link.filter_bots is True
        post_edit(web, link, include_bots='on')
        link.refresh_from_db()
        assert link.filter_bots is False


class TestEditPassword:
    @pytest.fixture
    def link(self, funded_user):
        return create_link(funded_user, code='Edit02', destination='https://example.com/old')

    def test_setting_one_asks_then_charges_once(self, web, funded_user, link):
        response = post_edit(web, link, password='hunter2')
        assert response.status_code == 422
        assert response['HX-Retarget'] == '#cost-confirm'
        assert 'Save changes for 1 credit' in response.content.decode()
        link.refresh_from_db()
        assert not link.password_hash and balance(funded_user) == 10

        assert post_edit(web, link, password='hunter2', confirm_cost='1').status_code == 200
        link.refresh_from_db()
        assert link.password_hash and link.password_paid_at is not None
        assert balance(funded_user) == 9

    def test_changing_it_later_is_free(self, web, funded_user, link):
        post_edit(web, link, password='first', confirm_cost='1')
        before = ShortLink.objects.get(pk=link.pk).password_hash
        assert post_edit(web, link, password='second').status_code == 200
        link.refresh_from_db()
        assert link.password_hash != before
        assert balance(funded_user) == 9

    def test_blank_keeps_the_password(self, web, funded_user, link):
        post_edit(web, link, password='first', confirm_cost='1')
        before = ShortLink.objects.get(pk=link.pk).password_hash
        post_edit(web, link, title='New title')
        link.refresh_from_db()
        assert link.password_hash == before and link.title == 'New title'

    def test_removing_it_and_setting_it_again_is_free(self, web, funded_user, link):
        post_edit(web, link, password='first', confirm_cost='1')
        post_edit(web, link, clear_password='on')
        link.refresh_from_db()
        assert link.password_hash is None and link.password_paid_at is not None
        assert post_edit(web, link, password='again').status_code == 200
        link.refresh_from_db()
        assert link.password_hash
        assert balance(funded_user) == 9

    def test_removing_wins_over_a_typed_password(self, web, funded_user, link):
        post_edit(web, link, password='first', confirm_cost='1')
        post_edit(web, link, password='second', clear_password='on')
        assert ShortLink.objects.get(pk=link.pk).password_hash is None

    def test_the_edit_form_offers_removal_only_when_there_is_a_password(self, web, link):
        url = reverse('short_url:edit', args=[link.pk])
        assert 'name="clear_password"' not in web.get(url).content.decode()
        post_edit(web, link, password='first', confirm_cost='1')
        content = web.get(url).content.decode()
        assert 'name="clear_password"' in content
        assert 'You already paid for this extra' in content

    def test_a_wrong_confirmation_asks_again_and_changes_nothing(self, web, funded_user, link):
        response = post_edit(web, link, password='hunter2', confirm_cost='0', title='Changed')
        assert response.status_code == 422
        assert response['HX-Retarget'] == '#cost-confirm'
        link.refresh_from_db()
        assert not link.password_hash and link.title != 'Changed'

    def test_a_price_changed_after_the_quote_asks_instead_of_charging(
        self, web, funded_user, link, monkeypatch, prices
    ):
        from ..services import quote_update as real_quote
        from ..views import form as form_module

        def quote_then_the_price_moves(link, **kwargs):
            result = real_quote(link, **kwargs)
            prices.password_price = 3
            prices.save()
            return result

        monkeypatch.setattr(form_module, 'quote_update', quote_then_the_price_moves)
        response = post_edit(web, link, password='hunter2', confirm_cost='1')
        assert response.status_code == 422
        assert (
            response['HX-Retarget'] == '#cost-confirm' and 'value="3"' in response.content.decode()
        )
        assert balance(funded_user) == 10
        assert not ShortLink.objects.get(pk=link.pk).password_hash

    def test_without_enough_credits_nothing_is_saved(self, client, user, prices):
        client.force_login(user)
        link = create_link(user, destination='https://example.com/old')
        response = post_edit(client, link, password='hunter2', title='Changed', confirm_cost='1')
        assert response.status_code == 422
        assert reverse('billing:credits-history') in response.content.decode()
        link.refresh_from_db()
        assert not link.password_hash and link.title != 'Changed'

    def test_a_bad_password_is_a_422(self, web, link):
        response = post_edit(web, link, password='x' * 200, confirm_cost='1')
        assert response.status_code == 422
        assert 'Password' in response.content.decode()
