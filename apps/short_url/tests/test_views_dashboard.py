from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from ..models import LinkStatus
from .factories import create_link, expired

pytestmark = pytest.mark.django_db

HX = {'HTTP_HX_REQUEST': 'true'}


class TestPage:
    def test_login_is_required(self, client):
        response = client.get(reverse('short_url:dashboard'))
        assert response.status_code == 302
        assert reverse('accounts:login') in response['Location']

    def test_the_list_partial_needs_a_login_too(self, client):
        # htmx gets the login page as a navigation, not a 302 it would swap in (issue #152).
        response = client.get(reverse('short_url:list'), **HX)
        assert response['HX-Redirect'].startswith(reverse('accounts:login'))

    def test_the_product_is_in_the_navigation(self, web):
        content = web.get(reverse('short_url:dashboard')).content.decode()
        assert 'Short URLs' in content
        assert reverse('short_url:dashboard') in content

    def test_an_empty_dashboard_invites_the_first_link(self, web):
        content = web.get(reverse('short_url:dashboard')).content.decode()
        assert 'No links yet' in content
        assert reverse('short_url:create') in content

    def test_a_row_shows_what_the_owner_needs(self, web, funded_user):
        link = create_link(
            funded_user,
            code='Promo1',
            destination='https://example.com/a-long-page',
            title='Spring catalogue',
            tags=['news', 'sale'],
            click_count=42,
        )
        content = web.get(reverse('short_url:dashboard')).content.decode()
        assert 'https://app.example.test/s/Promo1' in content
        assert 'https://example.com/a-long-page' in content
        assert 'Spring catalogue' in content
        assert '>news<' in content and '>sale<' in content
        assert '>42<' in content
        assert 'Active' in content
        assert reverse('short_url:detail', args=[link.pk]) in content
        assert reverse('short_url:edit', args=[link.pk]) in content
        assert reverse('short_url:qr', args=[link.pk]) in content
        assert '/s/Promo1+' in content
        assert 'x-data="copyText"' in content

    def test_the_row_menu_has_every_action(self, web, funded_user):
        link = create_link(funded_user)
        content = web.get(reverse('short_url:dashboard')).content.decode()
        for label in ('Stats', 'Edit', 'QR code', 'Preview', 'Disable', 'Delete'):
            assert label in content
        assert reverse('short_url:disable', args=[link.pk]) in content
        assert reverse('short_url:delete', args=[link.pk]) in content
        assert 'x-data="confirmModal"' in content

    def test_a_disabled_row_offers_enable_instead(self, web, funded_user):
        link = create_link(funded_user, status=LinkStatus.DISABLED)
        content = web.get(reverse('short_url:dashboard')).content.decode()
        assert reverse('short_url:enable', args=[link.pk]) in content
        assert reverse('short_url:disable', args=[link.pk]) not in content
        assert 'Disabled' in content

    def test_enabling_a_link_that_costs_credits_asks_first(self, web, funded_user):
        create_link(funded_user)
        create_link(funded_user)  # the free allowance is 2
        link = create_link(funded_user, status=LinkStatus.DISABLED)
        content = web.get(reverse('short_url:dashboard')).content.decode()
        assert 'data-cost="1"' in content
        assert 'Enable for 1 credit' in content
        assert link.code in content

    def test_enabling_a_free_link_posts_directly(self, web, funded_user):
        create_link(funded_user, status=LinkStatus.DISABLED)
        content = web.get(reverse('short_url:dashboard')).content.decode()
        assert 'data-cost' not in content

    def test_only_my_links_and_never_deleted_ones(self, web, funded_user, other_user):
        mine = create_link(funded_user, code='mine1')
        create_link(funded_user, code='gone1', status=LinkStatus.DELETED)
        create_link(other_user, code='theirs1')
        response = web.get(reverse('short_url:dashboard'))
        assert [link.pk for link in response.context['links']] == [mine.pk]
        content = response.content.decode()
        assert 'mine1' in content
        assert 'gone1' not in content
        assert 'theirs1' not in content

    def test_titles_and_destinations_are_escaped(self, web, funded_user):
        create_link(
            funded_user,
            title='<script>alert(1)</script>',
            destination='https://example.com/?a="><img src=x>',
        )
        content = web.get(reverse('short_url:dashboard')).content.decode()
        assert '<script>alert(1)</script>' not in content
        assert '<img src=x>' not in content


class TestSearchAndFilters:
    @pytest.fixture
    def links(self, funded_user):
        return {
            'alpha': create_link(
                funded_user,
                code='Alpha1',
                destination='https://one.example.com/x',
                title='First page',
                tags=['news'],
            ),
            'beta': create_link(
                funded_user,
                code='beta22',
                destination='https://two.example.org/y',
                title='Second page',
                tags=['news', 'sale'],
            ),
            'gamma': create_link(
                funded_user,
                code='gam333',
                destination='https://three.example.net/z',
                title='Third',
                tags=['sale-2'],
            ),
        }

    def listed(self, web, **query):
        response = web.get(reverse('short_url:dashboard'), query)
        return {link.code for link in response.context['links']}

    @pytest.mark.parametrize(
        ('q', 'expected'),
        [
            ('alpha', {'Alpha1'}),
            ('ALPHA1', {'Alpha1'}),
            ('two.example', {'beta22'}),
            ('Third', {'gam333'}),
            ('first page', {'Alpha1'}),
            ('sale', {'beta22', 'gam333'}),
            ('example', {'Alpha1', 'beta22', 'gam333'}),
            ('nothing-like-this', set()),
        ],
    )
    def test_search_covers_code_destination_title_and_tags(self, web, links, q, expected):
        assert self.listed(web, q=q) == expected

    @pytest.mark.parametrize('q', ['"', ',', '[', ']', '",', '["'])
    def test_json_punctuation_does_not_match_every_tagged_link(self, web, links, q):
        assert self.listed(web, q=q) == set()

    def test_a_tag_search_ignores_case(self, web, links):
        assert self.listed(web, q='SALE-2') == {'gam333'}

    def test_the_tag_list_scan_is_bounded(self, web, funded_user, monkeypatch):
        import sys

        # `views.dashboard` the module is shadowed by the view function of the same name.
        monkeypatch.setattr(sys.modules['apps.short_url.views.dashboard'], 'TAG_SCAN_LIMIT', 2)
        for n in range(4):
            create_link(funded_user, code=f'bound{n}', tags=[f'tag{n}'])
        response = web.get(reverse('short_url:dashboard'))
        assert len(response.context['tags']) == 2

    def test_filter_by_tag_is_exact(self, web, links):
        assert self.listed(web, tag='news') == {'Alpha1', 'beta22'}
        assert self.listed(web, tag='sale') == {'beta22'}
        assert self.listed(web, tag='sale-2') == {'gam333'}
        assert self.listed(web, tag='SALE') == {'beta22'}
        assert self.listed(web, tag='sal') == set()

    def test_search_and_tag_combine(self, web, links):
        assert self.listed(web, tag='news', q='second') == {'beta22'}

    def test_the_tag_filter_lists_my_tags_only(self, web, funded_user, other_user, links):
        create_link(other_user, tags=['secret'])
        response = web.get(reverse('short_url:dashboard'))
        assert response.context['tags'] == ['news', 'sale', 'sale-2']

    def test_tags_with_json_special_characters_are_safe(self, web, funded_user):
        create_link(funded_user, code='odd111', tags=['a-b_c'])
        assert self.listed(web, tag='a-b_c') == {'odd111'}
        assert self.listed(web, tag='a"b') == set()

    def test_sorting(self, web, funded_user):
        old = create_link(funded_user, code='old111', click_count=5)
        mid = create_link(funded_user, code='mid111', click_count=50)
        new = create_link(funded_user, code='new111', click_count=1)
        from ..models import ShortLink

        now = timezone.now()
        for n, link in enumerate((old, mid, new)):
            ShortLink.objects.filter(pk=link.pk).update(created_at=now - timedelta(days=3 - n))

        def order(sort):
            response = web.get(reverse('short_url:dashboard'), {'sort': sort} if sort else {})
            return [link.code for link in response.context['links']]

        assert order(None) == ['new111', 'mid111', 'old111']
        assert order('-created_at') == ['new111', 'mid111', 'old111']
        assert order('created_at') == ['old111', 'mid111', 'new111']
        assert order('-click_count') == ['mid111', 'old111', 'new111']
        assert order('click_count') == ['new111', 'old111', 'mid111']
        assert order('bogus') == ['new111', 'mid111', 'old111']

    def test_a_bad_filter_value_falls_back_to_the_default(self, web, links):
        response = web.get(reverse('short_url:dashboard'), {'status': 'bogus', 'sort': 'x'})
        assert response.status_code == 200
        assert len(response.context['links']) == 3


class TestStatusTabs:
    @pytest.fixture
    def states(self, funded_user):
        now = timezone.now()
        return {
            'active': create_link(funded_user, code='act111'),
            'active_future': create_link(
                funded_user,
                code='fut111',
                expires_at=now + timedelta(days=1),
                max_clicks=5,
                click_count=4,
            ),
            'disabled': create_link(funded_user, code='dis111', status=LinkStatus.DISABLED),
            'expired': expired(funded_user, code='exp111'),
            'capped': create_link(funded_user, code='cap111', max_clicks=2, click_count=2),
            'expired_and_capped': expired(funded_user, code='both11', max_clicks=1, click_count=1),
            'taken_down': create_link(funded_user, code='tkn111', status=LinkStatus.TAKEN_DOWN),
            'held': create_link(funded_user, code='hld111', held_for_review_at=now),
            'deleted': create_link(funded_user, code='del111', status=LinkStatus.DELETED),
        }

    def listed(self, web, tab):
        response = web.get(reverse('short_url:dashboard'), {'status': tab} if tab else {})
        return {link.code for link in response.context['links']}

    def test_each_tab_shows_its_state(self, web, states):
        assert self.listed(web, 'active') == {'act111', 'fut111'}
        assert self.listed(web, 'disabled') == {'dis111'}
        assert self.listed(web, 'expired') == {'exp111', 'both11'}
        assert self.listed(web, 'capped') == {'cap111'}
        assert self.listed(web, 'taken_down') == {'tkn111'}

    def test_all_shows_everything_but_deleted(self, web, states):
        assert self.listed(web, None) == {
            'act111',
            'fut111',
            'dis111',
            'exp111',
            'cap111',
            'both11',
            'tkn111',
            'hld111',
        }

    def test_the_tabs_agree_with_the_status_pills(self, web, states):
        from ..services import display_state

        for tab in ('active', 'disabled', 'expired', 'capped', 'taken_down'):
            response = web.get(reverse('short_url:dashboard'), {'status': tab})
            assert {display_state(link) for link in response.context['links']} == {tab}

    def test_counts(self, web, states):
        response = web.get(reverse('short_url:dashboard'))
        counts = {tab['key']: tab['count'] for tab in response.context['tabs']}
        assert counts == {
            'all': 8,
            'active': 2,
            'disabled': 1,
            'expired': 2,
            'capped': 1,
            'taken_down': 1,
        }

    def test_counts_only_cover_my_links(self, web, other_user, states):
        for _ in range(3):
            create_link(other_user)
        counts = {
            t['key']: t['count'] for t in web.get(reverse('short_url:dashboard')).context['tabs']
        }
        assert counts['all'] == 8

    def test_the_active_tab_is_marked(self, web, states):
        response = web.get(reverse('short_url:dashboard'), {'status': 'expired'})
        assert [t['key'] for t in response.context['tabs'] if t['active']] == ['expired']
        assert 'aria-current="page"' in response.content.decode()


class TestPagination:
    def test_25_per_page(self, web, funded_user):
        for i in range(27):
            create_link(funded_user, code=f'pg{i:04d}')
        first = web.get(reverse('short_url:dashboard'))
        assert len(first.context['links']) == 25
        assert 'Page 1 of 2' in first.content.decode()
        second = web.get(reverse('short_url:dashboard'), {'page': 2})
        assert len(second.context['links']) == 2
        assert 'Page 2 of 2' in second.content.decode()

    def test_the_pager_keeps_the_filters(self, web, funded_user):
        for i in range(27):
            create_link(funded_user, code=f'pg{i:04d}', tags=['x'])
        content = web.get(reverse('short_url:dashboard'), {'tag': 'x', 'q': 'pg'}).content.decode()
        assert 'page=2' in content
        assert 'tag=x' in content and 'q=pg' in content

    def test_a_page_out_of_range_shows_the_last(self, web, funded_user):
        create_link(funded_user)
        assert web.get(reverse('short_url:dashboard'), {'page': 99}).status_code == 200


class TestHtmx:
    def test_the_list_partial_is_not_a_full_page(self, web, funded_user):
        create_link(funded_user, code='part11')
        response = web.get(reverse('short_url:list'), **HX)
        content = response.content.decode()
        assert 'part11' in content
        assert '<html' not in content
        assert 'id="link-tabs"' in content and 'hx-swap-oob="true"' in content

    def test_it_pushes_the_dashboard_url_not_its_own(self, web):
        response = web.get(reverse('short_url:list'), {'status': 'active', 'q': 'x'}, **HX)
        pushed = response['HX-Push-Url']
        assert pushed.startswith(reverse('short_url:dashboard'))
        assert 'status=active' in pushed and 'q=x' in pushed
        assert '/list/' not in pushed

    def test_the_pushed_url_has_no_query_without_filters(self, web):
        assert web.get(reverse('short_url:list'), **HX)['HX-Push-Url'] == reverse(
            'short_url:dashboard'
        )

    def test_opened_directly_it_goes_to_the_full_page(self, web):
        response = web.get(reverse('short_url:list'), {'status': 'active', 'page': 2})
        assert response.status_code == 302
        assert response['Location'].startswith(reverse('short_url:dashboard'))
        assert 'status=active' in response['Location']

    def test_the_full_page_and_the_partial_list_the_same_rows(self, web, funded_user):
        for i in range(3):
            create_link(funded_user, code=f'same{i}1')
        full = web.get(reverse('short_url:dashboard'), {'q': 'same'})
        partial = web.get(reverse('short_url:list'), {'q': 'same'}, **HX)
        assert [link.pk for link in full.context['links']] == [
            link.pk for link in partial.context['links']
        ]
