"""The app's registration (product, job, signal receiver, migrations), the template tags and the
admin."""

from datetime import date, time, timedelta
from html import unescape

import pytest
from django.apps import apps as django_apps
from django.template import Context, Template
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.billing.signals import credits_added
from apps.core.products import get_products
from apps.core.scheduler import get_jobs

from ..templatetags.todo_ui import day_heading, due_text
from .factories import make_label, make_task

pytestmark = pytest.mark.django_db


class TestWiring:
    def test_the_product_is_registered(self):
        product = {p.label: p for p in get_products()}['todo']

        assert (product.name, product.url_name, product.icon) == (
            'To-do',
            'todo:today',
            'fas fa-check-square',
        )
        assert reverse(product.url_name) == '/todo/'

    def test_the_landing_page_and_nav_link_to_it(self, user):
        client = Client()
        client.force_login(user)

        assert reverse('todo:today') in client.get('/').content.decode()

    def test_the_purge_job_is_registered_daily(self):
        # `ready()` registers it at startup; the core scheduler tests clear the global registry, so
        # re-run that step (registering a name again just replaces it) whatever ran before.
        django_apps.get_app_config('todo')._register_jobs()
        specs = {job.name: job for job in get_jobs()}

        assert specs['todo.purge_trash'].interval == timedelta(hours=24)
        assert specs['todo.purge_trash'].lease == timedelta(hours=1)

    def test_the_credits_receiver_is_connected_once(self):
        receivers = [
            r for r in credits_added.receivers if r[0][0] == 'todo.settle_on_credits_added'
        ]

        assert len(receivers) == 1


class TestTags:
    @pytest.mark.parametrize(
        ('days', 'expected'),
        [
            (0, 'Today'),
            (1, 'Tomorrow'),
            (2, 'Friday'),
            (6, 'Tuesday'),
            (7, '4 Nov'),
            (-3, '25 Oct'),
        ],
    )
    def test_due_text(self, days, expected):
        today = date(2026, 10, 28)  # a Wednesday

        assert due_text(today + timedelta(days=days), today) == expected

    def test_another_year_shows_the_year(self):
        assert due_text(date(2027, 1, 5), date(2026, 12, 20)) == '5 Jan 2027'
        assert due_text(date(2025, 12, 30), date(2026, 1, 2)) == '30 Dec 2025'

    def test_day_heading(self):
        today = date(2026, 10, 28)

        assert day_heading(date(2026, 10, 29), today) == 'Tomorrow, 29 Oct'
        assert day_heading(date(2026, 11, 3), today) == 'Tuesday 3 Nov'

    def render(self, source: str, **context) -> str:
        request = RequestFactory().get('/')
        request.user = context.pop('user', None)
        return Template('{% load todo_ui %}' + source).render(
            Context({'request': request, **context})
        )

    def test_due_badge(self, user, wednesday):
        overdue = make_task(user, due_date=date(2026, 10, 20))
        today = make_task(user, due_date=wednesday, due_time=time(9, 5))
        later = make_task(user, due_date=date(2026, 12, 1))
        undated = make_task(user)

        for task, text, red in ((overdue, '20 Oct', True), (today, 'Today 09:05', False)):
            html = self.render('{% due_badge task %}', task=task, user=user)
            assert text in html
            assert ('text-red-600' in html) is red
        assert '1 Dec' in self.render('{% due_badge task %}', task=later, user=user)
        assert self.render('{% due_badge task %}', task=undated, user=user) == ''
        assert 'overdue' in self.render('{% due_badge task %}', task=overdue, user=user)

    def test_priority_flag(self, user):
        for priority, color in ((1, 'text-red-600'), (2, 'text-amber-500'), (3, 'text-sky-600')):
            html = self.render('{% priority_flag task %}', task=make_task(user, priority=priority))
            assert color in html and f'Priority {priority}' in html and 'dark:' in html
        assert self.render('{% priority_flag task %}', task=make_task(user)) == ''

    def test_label_chip_uses_its_colour_at_20_percent_and_escapes(self, user):
        label = make_label(user, 'A<b>', color='#336699', description='say "hi" <x>')

        html = self.render('{% label_chip label %}', label=label)

        assert 'background-color: #33669933; color: #336699' in html
        assert 'A&lt;b&gt;' in html and '<b>' not in html
        assert 'title="say &quot;hi&quot; &lt;x&gt;"' in html

    def test_a_chip_without_a_description_has_no_tooltip(self, user):
        assert 'title=' not in self.render('{% label_chip label %}', label=make_label(user, 'A'))

    def test_render_description_falls_back_when_too_long(self, user):
        task = make_task(user, description='x' * 60_000)

        html = self.render('{% render_description task %}', task=task, user=user)

        assert html.startswith('<p>') and unescape(html).count('x') == 60_000


class TestAdmin:
    @pytest.fixture
    def admin_client(self):
        client = Client()
        client.force_login(UserFactory(is_staff=True, is_superuser=True))
        return client

    def test_the_changelists_load_and_search(self, admin_client, user):
        make_task(user, number=1234, title='Findable task', unpaid_at=None)
        make_label(user, 'Home')

        for model in ('task', 'todoaccount', 'label'):
            url = reverse(f'admin:todo_{model}_changelist')
            assert admin_client.get(url).status_code == 200
        found = admin_client.get(reverse('admin:todo_task_changelist'), {'q': '1234'})
        assert 'Findable task' in found.content.decode()
        assert (
            admin_client.get(reverse('admin:todo_task_changelist'), {'completed': 'no'}).status_code
            == 200
        )
        assert (
            admin_client.get(reverse('admin:todo_task_changelist'), {'trashed': 'yes'}).status_code
            == 200
        )

    def test_the_read_only_models_cannot_be_added_or_deleted(self, admin_client, user):
        task = make_task(user)

        assert admin_client.get(reverse('admin:todo_task_add')).status_code == 403
        assert (
            admin_client.get(reverse('admin:todo_task_delete', args=[task.pk])).status_code == 403
        )
        assert admin_client.get(reverse('admin:todo_label_add')).status_code == 403

    def test_the_settings_singleton_goes_straight_to_its_form(self, admin_client):
        response = admin_client.get(reverse('admin:todo_todosettings_changelist'))

        assert response.status_code == 302
        form = admin_client.get(response['Location'])
        assert form.status_code == 200 and 'Task price (credits)' in form.content.decode()

    def test_staff_cannot_open_the_settings(self, user):
        client = Client()
        staff = UserFactory(is_staff=True)
        client.force_login(staff)

        assert client.get(reverse('admin:todo_todosettings_changelist')).status_code in (302, 403)
        assert client.get('/admin/todo/todosettings/1/change/').status_code == 403

    def test_deleting_a_user_with_tasks_is_not_blocked(self, admin_client, user):
        make_task(user)
        make_label(user, 'Home')

        response = admin_client.get(reverse('admin:accounts_user_delete', args=[user.pk]))

        assert response.status_code == 200
        assert 'Cannot delete' not in response.content.decode()
