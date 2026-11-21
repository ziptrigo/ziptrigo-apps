"""The list To-do opens on (`TodoAccount.default_view`): `/todo/` redirects to it, the settings
page and `PATCH /api/todo/settings` change it."""

import re
from html import unescape

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from ..models import DefaultView, TodoAccount
from ..services import get_account
from ..services.accounts import set_default_view
from .api_support import auth, get, limit_rule, patch  # noqa: F401 -- `auth` is a fixture

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


@pytest.fixture
def web(funded_user):
    client = Client()
    client.force_login(funded_user)
    return client


def text(response) -> str:
    return unescape(response.content.decode())


def heading(response) -> str:
    """The page's `<h1>` text."""
    found = re.search(r'<h1[^>]*>(.*?)</h1>', response.content.decode(), flags=re.DOTALL)
    assert found is not None
    return unescape(found.group(1)).strip()


def current_nav(response) -> str:
    """The `href` of the sidebar entry marked as the current page."""
    found = re.search(r'<a href="([^"]+)" aria-current="page"', response.content.decode())
    assert found is not None
    return found.group(1)


def options(response) -> dict[str, bool]:
    """The "Open To-do on" select: `{value: selected}`, in order."""
    body = response.content.decode()
    select = re.search(r'<select id="default-view".*?</select>', body, flags=re.DOTALL)
    assert select is not None
    return {
        value: bool(selected.strip())
        for value, selected in re.findall(
            r'<option value="([^"]+)"\s*(selected)?\s*>', select.group(0)
        )
    }


class TestModel:
    def test_today_is_the_default_in_python_and_the_database(self):
        field = TodoAccount._meta.get_field('default_view')  # ty: ignore[missing-attribute]

        assert field.default == DefaultView.TODAY
        assert field.db_default == DefaultView.TODAY

    def test_a_new_account_opens_on_today(self, funded_user):
        assert get_account(funded_user).default_view == 'today'

    def test_an_unknown_view_is_refused(self, funded_user):
        account = get_account(funded_user)

        with pytest.raises(ValidationError) as caught:
            set_default_view(account, 'closed')

        assert caught.value.message_dict == {'default_view': ['Choose Today, Upcoming or All.']}
        account.refresh_from_db()
        assert account.default_view == 'today'


class TestHome:
    def test_today_has_its_own_address(self):
        assert reverse('todo:home') == '/todo/'
        assert reverse('todo:today') == '/todo/today/'

    @pytest.mark.parametrize('view', ['today', 'upcoming', 'all'])
    def test_home_redirects_to_the_chosen_view(self, web, funded_user, view):
        set_default_view(get_account(funded_user), view)

        response = web.get('/todo/')

        assert response.status_code == 302
        assert response['Location'] == reverse(f'todo:{view}')

    def test_today_by_default(self, web):
        response = web.get('/todo/', follow=True)

        assert response.redirect_chain == [(reverse('todo:today'), 302)]
        assert response.status_code == 200
        assert heading(response) == 'Today'
        assert current_nav(response) == reverse('todo:today')

    @pytest.mark.parametrize(('view', 'title'), [('upcoming', 'Upcoming'), ('all', 'All tasks')])
    def test_the_chosen_page_opens(self, web, funded_user, view, title):
        set_default_view(get_account(funded_user), view)

        response = web.get('/todo/', follow=True)

        assert response.status_code == 200
        assert heading(response) == title
        assert current_nav(response) == reverse(f'todo:{view}')

    def test_the_query_string_is_kept(self, web, funded_user):
        set_default_view(get_account(funded_user), 'all')

        response = web.get('/todo/', {'sort': 'priority'})

        assert response['Location'] == reverse('todo:all') + '?sort=priority'

    def test_the_today_page_opens_whatever_the_setting(self, web, funded_user):
        set_default_view(get_account(funded_user), 'upcoming')

        response = web.get(reverse('todo:today'))

        assert response.status_code == 200
        assert heading(response) == 'Today'
        assert current_nav(response) == reverse('todo:today')

    def test_a_visitor_without_a_session_is_sent_to_sign_in(self):
        response = Client().get('/todo/')

        assert response.status_code == 302
        assert reverse('accounts:login') in response['Location']

    def test_the_sidebar_links_today_to_its_own_page(self, web, funded_user):
        set_default_view(get_account(funded_user), 'all')

        body = web.get(reverse('todo:all')).content.decode()

        assert f'href="{reverse("todo:today")}"' in body


class TestBackToTodo:
    def test_a_generic_way_back_lands_on_the_chosen_list(self, web, funded_user):
        set_default_view(get_account(funded_user), 'upcoming')

        response = web.post(
            reverse('todo:create'),
            {'title': 'T'},
            HTTP_REFERER='https://evil.test/',
            follow=True,
        )

        assert [url for url, _ in response.redirect_chain] == [
            reverse('todo:home'),
            reverse('todo:upcoming'),
        ]
        assert heading(response) == 'Upcoming'


class TestSettingsPage:
    def test_the_page_lists_the_three_choices_with_the_saved_one_selected(self, web, funded_user):
        set_default_view(get_account(funded_user), 'upcoming')

        response = web.get(reverse('todo:settings'))

        assert 'Open To-do on' in text(response)
        assert options(response) == {'today': False, 'upcoming': True, 'all': False}

    def test_the_choice_is_saved(self, web, funded_user):
        response = web.post(
            reverse('todo:settings'), {'default_due_time': '09:00', 'default_view': 'all'}, **HTMX
        )

        assert response.status_code == 200
        assert get_account(funded_user).default_view == 'all'

    def test_a_post_without_it_leaves_it_alone(self, web, funded_user):
        set_default_view(get_account(funded_user), 'upcoming')

        web.post(reverse('todo:settings'), {'default_due_time': '10:00'}, **HTMX)

        assert get_account(funded_user).default_view == 'upcoming'

    def test_an_unknown_choice_is_a_422(self, web, funded_user):
        response = web.post(
            reverse('todo:settings'), {'default_due_time': '09:00', 'default_view': 'trash'}, **HTMX
        )

        assert response.status_code == 422
        assert 'Open To-do on: Choose Today, Upcoming or All.' in text(response)
        assert get_account(funded_user).default_view == 'today'


class TestApi:
    def test_round_trips(self, client, auth, funded_user):
        for value in ('upcoming', 'all', 'today'):
            body = patch(client, '/settings', auth, {'default_view': value})
            assert body.status_code == 200 and body.json()['default_view'] == value
            assert get_account(funded_user).default_view == value
            assert get(client, '/settings', auth).json()['default_view'] == value

    def test_leaves_the_others_alone(self, client, auth, funded_user):
        patch(client, '/settings', auth, {'muted_overdue': 'hidden'})
        body = patch(client, '/settings', auth, {'default_view': 'all'}).json()

        assert body['muted_overdue'] == 'hidden' and body['default_view'] == 'all'

    @pytest.mark.parametrize('value', ['closed', '', None])
    def test_a_bad_value_is_a_400_and_spends_nothing(
        self, client, auth, funded_user, settings, value
    ):
        limit_rule(settings, 'TODO_EDIT_USER')

        response = patch(client, '/settings', auth, {'default_view': value})

        assert response.status_code == 400
        assert 'default_view: Choose Today, Upcoming or All.' in response.json()['detail']
        assert get_account(funded_user).default_view == 'today'
        assert patch(client, '/settings', auth, {'default_view': 'all'}).status_code == 200
