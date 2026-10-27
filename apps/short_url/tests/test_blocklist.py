"""The destination block list: value rules, matching (exact, wildcard, expiry), where it is
enforced (create and edit, every public page) and the admin convenience that adds entries."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import BlockedDomain, normalize_blocked_value
from ..services import (
    BlockedDomainError,
    LinkInput,
    blocklist,
    create_link,
    is_available,
    is_destination_blocked,
    update_link,
)
from .factories import create_link as make_link
from .factories import make_user

pytestmark = pytest.mark.django_db


def block(value: str, **fields) -> BlockedDomain:
    return BlockedDomain.objects.create(value=value, **fields)


class TestNormalizeValue:
    @pytest.mark.parametrize(
        ('raw', 'expected'),
        [
            ('Evil.Example.COM', 'evil.example.com'),
            ('  evil.example.com. ', 'evil.example.com'),
            ('*.Example.com', '*.example.com'),
            ('bücher.example', 'xn--bcher-kva.example'),
            ('*.bücher.example', '*.xn--bcher-kva.example'),
            ('203.0.113.9', '203.0.113.9'),
            ('2001:DB8::1', '2001:db8::1'),
            ('[2001:db8::1]', '2001:db8::1'),
            ('my_host.example.com', 'my_host.example.com'),
        ],
    )
    def test_the_stored_form(self, raw, expected):
        assert normalize_blocked_value(raw) == expected

    @pytest.mark.parametrize(
        'raw',
        [
            '',
            '   ',
            '*',
            '*.',
            '*.com',  # a whole top-level domain
            '*.203.0.113.9',  # no wildcard over an address
            'a*.example.com',
            'example.com/path',
            'user@example.com',
            'exa mple.com',
            'example..com',
            '-bad.example.com',
            'x' * 64 + '.example.com',
        ],
    )
    def test_refused_values(self, raw):
        with pytest.raises(ValidationError) as caught:
            normalize_blocked_value(raw)
        assert 'value' in caught.value.message_dict


class TestMatching:
    def test_an_exact_entry_blocks_only_that_host(self):
        block('evil.example.com')
        assert blocklist.is_blocked('evil.example.com')
        assert blocklist.is_blocked('EVIL.example.com')
        assert not blocklist.is_blocked('example.com')
        assert not blocklist.is_blocked('www.evil.example.com')
        assert not blocklist.is_blocked('notevil.example.com')

    def test_a_wildcard_blocks_the_bare_host_and_every_subdomain(self):
        block('*.evil.com')
        for host in ('evil.com', 'www.evil.com', 'a.b.c.evil.com'):
            assert blocklist.is_blocked(host), host

    def test_a_wildcard_does_not_match_a_longer_name_or_a_parent(self):
        block('*.evil.com')
        for host in ('notevil.com', 'evil.com.example.org', 'com', 'evil.co'):
            assert not blocklist.is_blocked(host), host

    def test_a_wildcard_does_not_reach_a_sibling_under_the_same_parent(self):
        block('*.a.example.com')
        assert not blocklist.is_blocked('b.example.com')
        assert not blocklist.is_blocked('example.com')

    def test_an_ip_address_matches_exactly(self):
        block('203.0.113.9')
        assert blocklist.is_blocked('203.0.113.9')
        assert not blocklist.is_blocked('203.0.113.10')
        block('2001:db8::1')
        assert blocklist.is_blocked('[2001:db8::1]')

    def test_an_expired_entry_does_not_block(self):
        block('old.example.com', expires_at=timezone.now() - timedelta(minutes=1))
        assert not blocklist.is_blocked('old.example.com')

    def test_an_entry_that_has_not_expired_yet_blocks(self):
        block('soon.example.com', expires_at=timezone.now() + timedelta(days=1))
        assert blocklist.is_blocked('soon.example.com')

    def test_an_empty_host_is_not_blocked(self):
        block('*.example.com')
        assert not blocklist.is_blocked('')

    def test_the_check_costs_one_query(self, django_assert_num_queries):
        block('*.evil.com')
        with django_assert_num_queries(1):
            blocklist.is_blocked('a.b.evil.com')

    def test_check_not_blocked_raises_a_neutral_error(self):
        block('evil.com')
        with pytest.raises(BlockedDomainError) as caught:
            blocklist.check_not_blocked('evil.com')
        assert caught.value.message_dict == {'destination': ['That address cannot be used.']}
        blocklist.check_not_blocked('fine.com')


class TestModelRules:
    def test_clean_normalizes_the_value(self):
        entry = BlockedDomain(value=' *.Evil.COM ')
        entry.clean()
        assert entry.value == '*.evil.com'

    def test_an_active_duplicate_is_refused(self):
        block('evil.com')
        with pytest.raises(ValidationError) as caught:
            BlockedDomain(value='EVIL.com').clean()
        assert 'already actively blocked' in caught.value.message_dict['value'][0]

    def test_an_expired_duplicate_is_fine(self):
        block('evil.com', expires_at=timezone.now() - timedelta(days=1))
        BlockedDomain(value='evil.com').clean()

    def test_editing_an_entry_is_not_its_own_duplicate(self):
        entry = block('evil.com')
        entry.clean()

    def test_save_normalizes_too(self):
        assert block('  Evil.COM ').value == 'evil.com'


class TestAddingFromALink:
    def test_the_default_blocks_the_host_and_its_subdomains(self, user):
        staff = make_user(is_staff=True)
        link = make_link(user, destination='https://www.evil.example.com/page')
        entry = blocklist.block_link_destination(link, created_by=staff, reason='phishing')
        assert entry is not None and entry.value == '*.www.evil.example.com'
        assert entry.created_by == staff and entry.reason == 'phishing'

    def test_without_subdomains_it_blocks_exactly_the_host(self, user):
        staff = make_user(is_staff=True)
        link = make_link(user, destination='https://evil.example.com/page')
        entry = blocklist.block_link_destination(link, created_by=staff, include_subdomains=False)
        assert entry is not None and entry.value == 'evil.example.com'
        assert blocklist.is_blocked('evil.example.com')
        assert not blocklist.is_blocked('a.evil.example.com')

    def test_an_ip_destination_is_blocked_exactly_whatever_was_asked(self, user):
        staff = make_user(is_staff=True)
        link = make_link(user, destination='http://203.0.113.9/x')
        entry = blocklist.block_link_destination(link, created_by=staff)
        assert entry is not None and entry.value == '203.0.113.9'

    def test_an_already_blocked_host_adds_nothing(self, user):
        staff = make_user(is_staff=True)
        link = make_link(user, destination='https://evil.example.com/')
        assert blocklist.block_link_destination(link, created_by=staff) is not None
        assert blocklist.block_link_destination(link, created_by=staff) is None
        assert BlockedDomain.objects.count() == 1

    def test_a_lapsed_entry_is_reactivated_not_duplicated(self, user):
        staff = make_user(is_staff=True)
        link = make_link(user, destination='https://evil.example.com/')
        block('*.evil.example.com', expires_at=timezone.now() - timedelta(days=1))
        entry = blocklist.block_link_destination(link, created_by=staff, reason='again')
        assert entry is not None and entry.expires_at is None and entry.reason == 'again'
        assert BlockedDomain.objects.count() == 1
        assert blocklist.is_blocked('evil.example.com')


class TestEnforcedAtCreateAndEdit:
    def test_create_refuses_a_blocked_destination(self, funded_user):
        block('*.evil.com')
        with pytest.raises(BlockedDomainError):
            create_link(funded_user, LinkInput(destination='https://www.evil.com/x'))

    def test_create_refuses_it_even_with_a_utm_builder(self, funded_user):
        block('evil.com')
        with pytest.raises(BlockedDomainError):
            create_link(
                funded_user,
                LinkInput(destination='https://evil.com/x', utm={'utm_source': 'mail'}),
            )

    def test_create_accepts_everything_else(self, funded_user):
        block('*.evil.com')
        link = create_link(funded_user, LinkInput(destination='https://good.com/x'))
        assert link.destination == 'https://good.com/x'

    def test_edit_refuses_a_new_blocked_destination(self, funded_user):
        link = create_link(funded_user, LinkInput(destination='https://good.com/x'))
        block('evil.com')
        with pytest.raises(BlockedDomainError):
            update_link(link, destination='https://evil.com/y')
        link.refresh_from_db()
        assert link.destination == 'https://good.com/x'

    def test_edit_can_move_a_blocked_link_somewhere_else(self, funded_user):
        link = create_link(funded_user, LinkInput(destination='https://good.com/x'))
        block('good.com')
        assert not is_available(link)
        update_link(link, destination='https://better.com/x')
        link.refresh_from_db()
        assert is_available(link)

    def test_edit_leaving_a_blocked_destination_alone_still_saves_other_fields(self, funded_user):
        link = create_link(funded_user, LinkInput(destination='https://good.com/x'))
        block('good.com')
        update_link(link, destination='https://good.com/x', title='New title')
        link.refresh_from_db()
        assert link.title == 'New title'
        assert not is_available(link)  # still not working

    def test_the_form_shows_the_refusal(self, web):
        block('evil.com')
        response = web.post('/links/new/', {'destination': 'https://evil.com/x'})
        assert response.status_code == 422
        assert 'Destination: That address cannot be used.' in response.content.decode()
        assert 'block' not in response.content.decode().lower()


class TestEnforcedOnPublicPages:
    @pytest.fixture
    def link(self, user):
        return make_link(user, code='Promo1', destination='https://shop.evil.com/x')

    def test_is_available_turns_false_when_the_host_is_blocked(self, link):
        assert is_available(link)
        block('*.evil.com')
        assert not is_available(link)
        assert is_destination_blocked(link)

    def test_it_is_asked_last(self, user, django_assert_num_queries):
        disabled = make_link(user, status='disabled')
        with django_assert_num_queries(0):
            assert not is_available(disabled)

    def test_the_redirect_renders_the_neutral_page_and_counts_nothing(self, client, link):
        assert client.get('/s/Promo1').status_code == 302
        block('*.evil.com')
        response = client.get('/s/Promo1')
        assert response.status_code == 404
        assert response.content == client.get('/s/neverissued').content
        link.refresh_from_db()
        assert link.click_count == 1

    def test_a_lapsed_block_lets_the_link_work_again(self, client, link):
        entry = block('*.evil.com')
        assert client.get('/s/Promo1').status_code == 404
        BlockedDomain.objects.filter(pk=entry.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        assert client.get('/s/Promo1').status_code == 302

    def test_the_preview_and_the_report_form_are_neutral_too(self, client, link):
        block('*.evil.com')
        unknown = client.get('/s/neverissued+')
        assert client.get('/s/Promo1+').content == unknown.content
        report = client.post('/s/Promo1/report', {'reason': 'phishing'})
        assert report.status_code == 404

    def test_the_password_page_is_neutral_too(self, client, user):
        from django.contrib.auth.hashers import make_password

        locked = make_link(
            user,
            code='Lock1',
            destination='https://shop.evil.com/x',
            password_hash=make_password('pw'),
        )
        assert client.get('/s/Lock1/unlock').status_code == 200
        block('*.evil.com')
        assert client.get('/s/Lock1/unlock').status_code == 404
        assert client.post('/s/Lock1/unlock/', {'password': 'pw'}).status_code == 404
        assert locked.pk

    def test_the_owner_page_says_it_isnt_working(self, web, funded_user):
        link = make_link(funded_user, destination='https://shop.evil.com/x')
        page = web.get(f'/links/{link.pk}/')
        assert 'Not working' not in page.content.decode()
        block('*.evil.com')
        content = web.get(f'/links/{link.pk}/').content.decode()
        assert "destination can't be used" in content
        assert 'Not working' in content
