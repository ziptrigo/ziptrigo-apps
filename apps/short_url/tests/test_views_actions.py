import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from ..models import LinkStatus, ShortLink
from .conftest import balance
from .factories import create_link

pytestmark = pytest.mark.django_db


def url(name, link):
    return reverse(f'short_url:{name}', args=[link.pk])


def flashes(response) -> list[str]:
    """The newest flash message, as a one-item list (earlier ones, from other requests of the
    same logged-in client, are still in the session until a page shows them)."""
    return [str(m) for m in get_messages(response.wsgi_request)][-1:]


def status_of(link) -> str:
    return ShortLink.objects.get(pk=link.pk).status


@pytest.fixture
def link(funded_user):
    return create_link(funded_user, code='Act001')


class TestMethodsAndOwnership:
    @pytest.mark.parametrize('action', ['enable', 'disable', 'delete'])
    def test_only_post(self, web, link, action):
        assert web.get(url(action, link)).status_code == 405

    @pytest.mark.parametrize('action', ['enable', 'disable', 'delete'])
    def test_login_is_required(self, client, link, action):
        response = client.post(url(action, link))
        assert response.status_code == 302
        assert reverse('accounts:login') in response['Location']
        assert status_of(link) == LinkStatus.ACTIVE

    @pytest.mark.parametrize('action', ['enable', 'disable', 'delete'])
    def test_someone_elses_link_is_a_404_and_untouched(self, web, other_user, action):
        theirs = create_link(other_user)
        assert web.post(url(action, theirs)).status_code == 404
        assert status_of(theirs) == LinkStatus.ACTIVE

    @pytest.mark.parametrize('action', ['enable', 'disable', 'delete'])
    def test_a_deleted_link_is_a_404(self, web, funded_user, action):
        gone = create_link(funded_user, status=LinkStatus.DELETED)
        assert web.post(url(action, gone)).status_code == 404


class TestDisableEnable:
    def test_disable(self, web, link):
        response = web.post(url('disable', link))
        assert response.status_code == 302
        assert response['Location'] == reverse('short_url:dashboard')
        assert flashes(response) == ['Link disabled.']
        assert status_of(link) == LinkStatus.DISABLED

    def test_a_disabled_link_stops_redirecting(self, web, client, link):
        web.post(url('disable', link))
        assert client.get('/s/Act001').status_code == 404

    def test_enable(self, web, funded_user):
        link = create_link(funded_user, status=LinkStatus.DISABLED, code='Dis001')
        response = web.post(url('enable', link))
        assert flashes(response) == ['Link enabled.']
        assert status_of(link) == LinkStatus.ACTIVE
        assert balance(funded_user) == 10

    def test_an_enabled_link_redirects_again(self, web, client, funded_user):
        link = create_link(funded_user, status=LinkStatus.DISABLED, code='Dis002')
        assert client.get('/s/Dis002').status_code == 404
        web.post(url('enable', link))
        assert client.get('/s/Dis002').status_code == 302

    def test_repeating_says_so(self, web, link):
        web.post(url('disable', link))
        assert flashes(web.post(url('disable', link))) == ['Link was already disabled.']
        web.post(url('enable', link))
        assert flashes(web.post(url('enable', link))) == ['Link was already enabled.']

    def test_next_is_followed_when_it_stays_on_the_site(self, web, link):
        response = web.post(url('disable', link), {'next': '/links/?status=active&q=x'})
        assert response['Location'] == '/links/?status=active&q=x'

    def test_next_from_the_query_string_works_for_the_modal(self, web, link):
        response = web.post(url('disable', link) + '?next=/links/%3Fstatus%3Dactive')
        assert response['Location'] == '/links/?status=active'

    @pytest.mark.parametrize(
        'target', ['https://evil.example/', '//evil.example/x', 'javascript:alert(1)']
    )
    def test_next_to_another_site_is_ignored(self, web, link, target):
        response = web.post(url('disable', link), {'next': target})
        assert response['Location'] == reverse('short_url:dashboard')

    def test_a_taken_down_link_cannot_be_toggled(self, web, funded_user):
        link = create_link(funded_user, status=LinkStatus.TAKEN_DOWN)
        for action in ('enable', 'disable'):
            response = web.post(url(action, link))
            assert flashes(response) == ['This link can no longer be changed.']
        assert status_of(link) == LinkStatus.TAKEN_DOWN


class TestEnableCosts:
    """The free allowance is 2 active links (see the `prices` fixture)."""

    @pytest.fixture
    def costly(self, funded_user):
        create_link(funded_user)
        create_link(funded_user)
        return create_link(funded_user, status=LinkStatus.DISABLED, code='Cost01')

    def test_it_will_not_charge_without_a_confirmation(self, web, funded_user, costly):
        response = web.post(url('enable', costly))
        assert flashes(response) == [
            'Enabling this link costs 1 credits. Confirm it from the menu to continue.'
        ]
        assert status_of(costly) == LinkStatus.DISABLED
        assert balance(funded_user) == 10

    @pytest.mark.parametrize('confirm', ['0', '2', 'x', ''])
    def test_a_wrong_confirmation_is_not_enough(self, web, funded_user, costly, confirm):
        web.post(url('enable', costly), {'confirm_cost': confirm})
        assert status_of(costly) == LinkStatus.DISABLED
        assert balance(funded_user) == 10

    def test_confirmed_it_charges_once(self, web, funded_user, costly):
        response = web.post(url('enable', costly), {'confirm_cost': '1'})
        assert flashes(response) == ['Link enabled.']
        assert status_of(costly) == LinkStatus.ACTIVE
        assert balance(funded_user) == 9

    def test_disabling_and_enabling_again_is_free(self, web, funded_user, costly):
        web.post(url('enable', costly), {'confirm_cost': '1'})
        web.post(url('disable', costly))
        response = web.post(url('enable', costly))  # no confirmation needed: nothing to pay
        assert flashes(response) == ['Link enabled.']
        assert balance(funded_user) == 9

    def test_without_credits_it_stays_disabled(self, client, user, prices):
        client.force_login(user)
        create_link(user)
        create_link(user)
        link = create_link(user, status=LinkStatus.DISABLED)
        response = client.post(url('enable', link), {'confirm_cost': '1'})
        assert "don't have enough credits" in flashes(response)[0]
        assert status_of(link) == LinkStatus.DISABLED

    def test_the_hard_cap_refuses(self, web, funded_user):
        for _ in range(5):
            create_link(funded_user)
        link = create_link(funded_user, status=LinkStatus.DISABLED)
        response = web.post(url('enable', link), {'confirm_cost': '1'})
        assert 'at most 5 active links' in flashes(response)[0]
        assert status_of(link) == LinkStatus.DISABLED


class TestDelete:
    def test_delete(self, web, link):
        response = web.post(url('delete', link))
        assert flashes(response) == ['Link deleted.']
        assert status_of(link) == LinkStatus.DELETED

    def test_it_leaves_the_dashboard_and_stops_redirecting(self, web, client, link):
        web.post(url('delete', link))
        assert link.code not in web.get(reverse('short_url:dashboard')).content.decode()
        assert client.get('/s/Act001').status_code == 404

    def test_the_code_stays_taken(self, web, link):
        web.post(url('delete', link))
        response = web.post(
            reverse('short_url:create'),
            {'destination': 'https://example.com/x', 'alias': 'Act001', 'confirm_cost': '1'},
            HTTP_HX_REQUEST='true',
        )
        assert response.status_code == 422
        assert 'not available' in response.content.decode()

    def test_going_back_to_its_own_page_would_404_so_it_goes_to_the_dashboard(self, web, link):
        response = web.post(url('delete', link), {'next': url('detail', link)})
        assert response['Location'] == reverse('short_url:dashboard')

    def test_going_back_to_a_filtered_dashboard_is_kept(self, web, link):
        response = web.post(url('delete', link), {'next': '/links/?status=active'})
        assert response['Location'] == '/links/?status=active'

    def test_a_taken_down_link_cannot_be_deleted(self, web, funded_user):
        link = create_link(funded_user, status=LinkStatus.TAKEN_DOWN)
        response = web.post(url('delete', link))
        assert flashes(response) == ['This link can no longer be changed.']
        assert status_of(link) == LinkStatus.TAKEN_DOWN
