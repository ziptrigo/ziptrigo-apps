"""The app's registration (product, job, migrations), the template tags and the admin."""

from datetime import timedelta
from html import unescape

import pytest
from django.apps import apps as django_apps
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.core.products import get_products
from apps.core.scheduler import get_jobs

from ..models import Note
from ..services import create_note, create_tag, trash
from ..templatetags.notes_ui import dark_mode_color, render_text, tag_chip
from .factories import make_note, make_tag

pytestmark = pytest.mark.django_db


class TestWiring:
    def test_the_product_is_registered(self):
        product = {p.label: p for p in get_products()}['notes']
        assert (product.name, product.url_name, product.icon) == (
            'Notes',
            'notes:list',
            'fas fa-note-sticky',
        )
        assert product.description == 'Markdown notes with code blocks, tags and share links.'
        assert reverse(product.url_name) == '/notes/'

    def test_the_landing_page_and_nav_link_to_it(self, user):
        client = Client()
        client.force_login(user)
        assert reverse('notes:list') in client.get('/').content.decode()

    def test_the_purge_job_is_registered_daily_with_a_description(self):
        # `ready()` registers it at startup; the core scheduler tests clear the global registry, so
        # re-run that step (registering a name again just replaces it) whatever ran before.
        django_apps.get_app_config('notes')._register_jobs()
        specs = {job.name: job for job in get_jobs()}
        spec = specs['notes.purge_trash']
        assert spec.interval == timedelta(hours=24) and spec.lease == timedelta(hours=1)
        assert spec.description.strip()
        job_names = [name for name in specs if name.startswith('notes.')]
        assert all(specs[name].description.strip() for name in job_names)

    def test_the_job_deletes_old_trash(self, user):
        from .. import jobs

        old = make_note(user, deleted_at=timezone.now() - timedelta(days=31))
        jobs.purge_trash()
        assert not Note.objects.filter(pk=old.pk).exists()

    def test_the_rate_limit_rules_exist(self, settings):
        for rule in ('NOTES_CREATE_USER', 'NOTES_EDIT_USER', 'NOTES_PREVIEW_USER'):
            assert rule in settings.RATELIMIT_RULES

    def test_every_markdown_call_uses_the_extended_profile(self):
        """A call of any `apps.core.markdown` function in Notes' own code passes
        `profile='extended'` (the default `basic` has no tables or colour)."""
        import ast
        import pathlib

        takes_profile = {
            'render',
            'to_plain_text',
            'search_text',
            'extract_references',
            'count_checklist_items',
            'toggle_checklist_item',
        }
        root = pathlib.Path(__file__).resolve().parents[1]
        bad = []
        for path in root.rglob('*.py'):
            if 'tests' in path.parts or 'migrations' in path.parts:
                continue
            tree = ast.parse(path.read_text())
            imported = {
                alias.asname or alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module == 'apps.core.markdown'
                for alias in node.names
                if alias.name in takes_profile
            }
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in imported
                ):
                    profiles = [
                        kw.value.value
                        for kw in node.keywords
                        if kw.arg == 'profile' and isinstance(kw.value, ast.Constant)
                    ]
                    if profiles != ['extended']:
                        bad.append(f'{path.name}:{node.lineno} {node.func.id}')
        assert bad == []


class TestTemplateTags:
    def test_render_text_is_the_extended_profile(self):
        html = render_text('```python\nx = 1\n```\n\n| a |\n|---|\n| 1 |')
        assert 'class="hl"' in html and '<table>' in html

    def test_notes_never_resolve_task_references(self):
        assert 'href' not in render_text('see #1234')

    def test_render_text_falls_back_when_too_long(self):
        html = render_text('<b>' + 'x' * 50_001)
        assert html.startswith('<p>&lt;b&gt;') and '<b>' not in html

    def test_tag_chip_uses_its_colour_at_20_percent_and_escapes(self, user):
        tag = make_tag(user, '<b>Work</b>', color='#336699', description='Office "things"')
        html = tag_chip(tag)
        assert 'background-color: #33669933' in html and 'color: #336699' in html
        assert '&lt;b&gt;Work&lt;/b&gt;' in html and 'title="Office &quot;things&quot;"' in html
        assert 'title=' not in tag_chip(make_tag(user, 'Plain'))

    def test_dark_mode_colour_is_lightened_until_readable(self):
        assert dark_mode_color('#0000ff') != '#0000ff'
        assert dark_mode_color('#ffffff') == '#ffffff'
        assert dark_mode_color('nonsense') == 'nonsense'


class TestAdmin:
    @pytest.fixture
    def admin_client(self):
        client = Client()
        client.force_login(UserFactory(is_staff=True, is_superuser=True))
        return client

    def test_the_changelists_load_and_search(self, admin_client, user, config):
        tag = create_tag(user, 'Work')
        note = create_note(user, title='Findable note', tags=[tag.pk])
        for model in ('note', 'tag', 'notesaccount'):
            assert admin_client.get(reverse(f'admin:notes_{model}_changelist')).status_code == 200
        found = admin_client.get(reverse('admin:notes_note_changelist'), {'q': 'Findable'})
        assert 'Findable note' in found.content.decode()
        for flt in (
            {'archived': 'no'},
            {'trashed': 'yes'},
            {'locked': 'yes'},
            {'is_free__exact': '1'},
        ):
            assert admin_client.get(reverse('admin:notes_note_changelist'), flt).status_code == 200
        detail = admin_client.get(reverse('admin:notes_note_change', args=[note.pk]))
        assert detail.status_code == 200 and 'Findable note' in unescape(detail.content.decode())

    def test_the_read_only_models_cannot_be_added_or_deleted(self, admin_client, user, config):
        note = create_note(user, title='A')
        assert admin_client.get(reverse('admin:notes_note_add')).status_code == 403
        assert (
            admin_client.get(reverse('admin:notes_note_delete', args=[note.pk])).status_code == 403
        )
        assert admin_client.get(reverse('admin:notes_tag_add')).status_code == 403
        assert (
            admin_client.post(
                reverse('admin:notes_note_change', args=[note.pk]), {'title': 'hacked'}
            ).status_code
            == 403
        )

    def test_the_settings_singleton_goes_straight_to_its_form(self, admin_client):
        response = admin_client.get(reverse('admin:notes_notessettings_changelist'))
        assert response.status_code == 302
        form = admin_client.get(response['Location'])
        text = form.content.decode()
        assert form.status_code == 200
        for label in ('Creation price (credits)', 'Renewal price (credits)', 'Free notes'):
            assert label in text

    def test_staff_cannot_open_the_settings(self, user):
        client = Client()
        client.force_login(UserFactory(is_staff=True))
        assert client.get(reverse('admin:notes_notessettings_changelist')).status_code in (302, 403)
        assert client.get('/admin/notes/notessettings/1/change/').status_code == 403

    def test_deleting_a_user_with_notes_is_not_blocked(self, admin_client, user, config):
        create_note(user, title='A')
        trash(create_note(user, title='B'))
        create_tag(user, 'Work')
        response = admin_client.get(reverse('admin:accounts_user_delete', args=[user.pk]))
        assert response.status_code == 200
        assert 'Cannot delete' not in response.content.decode()
        confirmed = admin_client.post(
            reverse('admin:accounts_user_delete', args=[user.pk]), {'post': 'yes'}
        )
        assert confirmed.status_code == 302
        assert not Note.objects.filter(user_id=user.pk).exists()

    def test_the_icon_is_configured(self, settings):
        assert settings.JAZZMIN_SETTINGS['icons']['notes.Note'] == 'fas fa-note-sticky'
