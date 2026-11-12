"""The Notes JWT API's version-history and share-link endpoints."""

import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from ..models import EditorKind, Note, NoteVersion, ShareLink
from ..services import Editor, create_note, links, save_note, takedown, trash
from .api_support import (  # noqa: F401 -- the fixtures are used by name
    auth,
    delete,
    get,
    limit_rule,
    other_auth,
    patch,
    post,
)
from .factories import make_link, make_note, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

VERSION_KEYS = {
    'id',
    'number',
    'title',
    'editor_kind',
    'editor_name',
    'via_link',
    'created_at',
    'updated_at',
}
LINK_KEYS = {
    'id',
    'url',
    'mode',
    'label',
    'expires_at',
    'revoked_at',
    'active',
    'created_at',
    'last_edited_at',
}


def history(note, owner):
    """A note with three versions: the owner's, then an anonymous link editor's, then a signed-in
    visitor's."""
    visitor = make_user(name='Vera Visitor')
    link = make_link(note, mode='edit')
    save_note(note, editor=Editor('anonymous', None, link), content='anon edit', base_version=1)
    note.refresh_from_db()
    save_note(note, editor=Editor('user', visitor, link), content='user edit', base_version=2)
    note.refresh_from_db()
    return link, visitor


class TestVersions:
    def test_the_list_is_newest_first_without_the_text(self, client, auth, funded_user):
        note = create_note(funded_user, title='T', content='first')
        link, visitor = history(note, funded_user)

        response = get(client, f'/notes/{note.pk}/versions', auth)

        assert response.status_code == 200
        body = response.json()
        assert [version['number'] for version in body] == [3, 2, 1]
        assert all(set(version) == VERSION_KEYS for version in body)
        newest = body[0]
        assert (newest['editor_kind'], newest['editor_name'], newest['via_link']) == (
            'user',
            'Vera Visitor',
            True,
        )
        assert (body[1]['editor_kind'], body[1]['editor_name'], body[1]['via_link']) == (
            'anonymous',
            None,
            True,
        )
        assert (body[2]['editor_kind'], body[2]['via_link']) == ('owner', False)
        assert body[2]['editor_name'] == funded_user.name or body[2]['editor_name'] is None

    def test_one_version_has_the_content(self, client, auth, funded_user):
        note = create_note(funded_user, title='T', content='first')
        history(note, funded_user)
        first = NoteVersion.objects.get(note=note, number=1)

        response = get(client, f'/notes/{note.pk}/versions/{first.pk}', auth)

        assert response.status_code == 200
        assert set(response.json()) == VERSION_KEYS | {'content'}
        assert response.json()['content'] == 'first'

    def test_a_version_of_another_note_is_a_404(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')
        other = create_note(funded_user, title='Other')
        version = NoteVersion.objects.get(note=other, number=1)

        assert get(client, f'/notes/{note.pk}/versions/{version.pk}', auth).status_code == 404
        assert get(client, f'/notes/{note.pk}/versions/{uuid.uuid4()}', auth).status_code == 404
        assert (
            post(client, f'/notes/{note.pk}/versions/{version.pk}/restore', auth).status_code == 404
        )

    def test_another_users_history_is_a_404(self, client, auth, other_user):
        note = create_note(other_user, title='T')
        version = NoteVersion.objects.get(note=note, number=1)

        assert get(client, f'/notes/{note.pk}/versions', auth).status_code == 404
        assert get(client, f'/notes/{note.pk}/versions/{version.pk}', auth).status_code == 404
        assert (
            post(client, f'/notes/{note.pk}/versions/{version.pk}/restore', auth).status_code == 404
        )

    def test_restoring_makes_a_new_save_and_keeps_what_it_replaced(self, client, auth, funded_user):
        note = create_note(funded_user, title='T', content='first')
        history(note, funded_user)
        first = NoteVersion.objects.get(note=note, number=1)

        response = post(client, f'/notes/{note.pk}/versions/{first.pk}/restore', auth)

        assert response.status_code == 200
        body = response.json()
        assert (body['content'], body['version']) == ('first', 4)
        numbers = list(NoteVersion.objects.filter(note=note).values_list('number', flat=True))
        assert sorted(numbers) == [1, 2, 3, 4]
        newest = NoteVersion.objects.get(note=note, number=4)
        assert (newest.editor_kind, newest.content) == (EditorKind.OWNER, 'first')
        assert NoteVersion.objects.get(note=note, number=3).content == 'user edit'

    def test_restoring_what_the_note_already_holds_changes_nothing(self, client, auth, funded_user):
        note = create_note(funded_user, title='T', content='first')
        newest = NoteVersion.objects.get(note=note, number=1)

        response = post(client, f'/notes/{note.pk}/versions/{newest.pk}/restore', auth)

        assert response.status_code == 200 and response.json()['version'] == 1

    def test_restoring_on_a_read_only_note(self, client, auth, funded_user):
        trashed = create_note(funded_user, title='T')
        locked = create_note(funded_user, title='L')
        down = create_note(funded_user, title='D')
        trash(trashed)
        Note.objects.filter(pk=locked.pk).update(locked_at=timezone.now())
        takedown.take_down_note(down, by=make_user(is_staff=True), reason='x')

        def restore(note):
            version = NoteVersion.objects.get(note=note, number=1)
            return post(client, f'/notes/{note.pk}/versions/{version.pk}/restore', auth)

        assert restore(trashed).status_code == 400
        assert restore(locked).status_code == 402
        assert restore(down).status_code == 409

    def test_a_trashed_note_history_can_be_read(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')
        trash(note)

        assert get(client, f'/notes/{note.pk}/versions', auth).status_code == 200

    def test_a_refused_restore_spends_no_budget_and_a_good_one_spends_one(
        self, client, auth, funded_user, settings
    ):
        note = create_note(funded_user, title='T', content='first')
        history(note, funded_user)
        first = NoteVersion.objects.get(note=note, number=1)
        Note.objects.filter(pk=note.pk).update(locked_at=timezone.now())
        limit_rule(settings, 'NOTES_EDIT_USER', limit=1)

        for _ in range(3):
            assert (
                post(client, f'/notes/{note.pk}/versions/{first.pk}/restore', auth).status_code
                == 402
            )
        Note.objects.filter(pk=note.pk).update(locked_at=None)
        assert (
            post(client, f'/notes/{note.pk}/versions/{first.pk}/restore', auth).status_code == 200
        )
        assert (
            post(client, f'/notes/{note.pk}/versions/{first.pk}/restore', auth).status_code == 429
        )


class TestLinks:
    def test_create_a_read_link(self, client, auth, funded_user, settings):
        settings.BASE_URL = 'https://app.example.com'
        note = create_note(funded_user, title='T')

        response = post(client, f'/notes/{note.pk}/links', auth, {})

        assert response.status_code == 201
        body = response.json()
        assert set(body) == LINK_KEYS
        link = ShareLink.objects.get(pk=body['id'])
        assert body['url'] == f'https://app.example.com/n/{link.code}'
        assert (body['mode'], body['label'], body['active']) == ('read', '', True)
        assert body['expires_at'] is None and body['revoked_at'] is None
        assert body['last_edited_at'] is None

    def test_an_editable_link_with_a_label(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')

        body = post(
            client, f'/notes/{note.pk}/links', auth, {'mode': 'edit', 'label': '  for   Sam '}
        ).json()

        assert (body['mode'], body['label']) == ('edit', 'for Sam')

    def test_a_bare_date_expires_at_the_end_of_that_day_in_the_owners_zone(
        self, client, auth, funded_user
    ):
        funded_user.timezone = 'America/Chicago'
        funded_user.save()
        note = create_note(funded_user, title='T')
        day = (timezone.now() + timedelta(days=10)).date()

        body = post(client, f'/notes/{note.pk}/links', auth, {'expires_at': day.isoformat()}).json()

        link = ShareLink.objects.get(pk=body['id'])
        assert links.expiry_date(link) == day
        local = link.expires_at.astimezone(ZoneInfo('America/Chicago'))
        assert (local.hour, local.minute, local.second) == (23, 59, 59)

    def test_a_date_time_is_that_instant(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')
        moment = (timezone.now() + timedelta(days=3)).replace(microsecond=0)

        body = post(
            client, f'/notes/{note.pk}/links', auth, {'expires_at': moment.isoformat()}
        ).json()

        assert ShareLink.objects.get(pk=body['id']).expires_at == moment

    def test_a_date_time_without_an_offset_is_read_in_the_owners_zone(
        self, client, auth, funded_user
    ):
        funded_user.timezone = 'America/Chicago'
        funded_user.save()
        note = create_note(funded_user, title='T')
        day = (timezone.now() + timedelta(days=5)).date()

        body = post(
            client, f'/notes/{note.pk}/links', auth, {'expires_at': f'{day.isoformat()}T18:00:00'}
        ).json()

        link = ShareLink.objects.get(pk=body['id'])
        local = link.expires_at.astimezone(ZoneInfo('America/Chicago'))
        assert (local.date(), local.hour) == (day, 18)

    @pytest.mark.parametrize(
        ('payload', 'message'),
        [
            ({'expires_at': '2020-01-01'}, 'expires_on: Choose a date that has not passed.'),
            ({'expires_at': '2020-01-01T10:00:00Z'}, 'expires_on: Choose a date'),
            ({'label': 'x' * 101}, 'label: Keep the label to 100 characters or fewer.'),
        ],
    )
    def test_a_refusal_is_a_400_and_spends_no_budget(
        self, client, auth, funded_user, settings, payload, message
    ):
        note = create_note(funded_user, title='T')
        limit_rule(settings, 'NOTES_EDIT_USER', limit=1)

        for _ in range(3):
            response = post(client, f'/notes/{note.pk}/links', auth, payload)
            assert response.status_code == 400
            assert message in response.json()['detail']
        assert post(client, f'/notes/{note.pk}/links', auth, {}).status_code == 201

    def test_at_most_ten_active_links(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')
        for _ in range(10):
            make_link(note)

        response = post(client, f'/notes/{note.pk}/links', auth, {})

        assert response.status_code == 400
        assert response.json()['detail'] == 'A note can have at most 10 active links.'

    def test_a_bad_mode_and_unknown_keys_are_422(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')

        for payload in ({'mode': 'admin'}, {'code': 'abc'}, {'expires_at': 'soon'}):
            assert post(client, f'/notes/{note.pk}/links', auth, payload).status_code == 422

    def test_refused_on_a_read_only_note(self, client, auth, funded_user):
        trashed = create_note(funded_user, title='T')
        trash(trashed)
        locked = make_note(funded_user, title='L', locked_at=timezone.now())
        down = create_note(funded_user, title='D')
        takedown.take_down_note(down, by=make_user(is_staff=True), reason='x')

        assert post(client, f'/notes/{trashed.pk}/links', auth, {}).status_code == 400
        assert post(client, f'/notes/{locked.pk}/links', auth, {}).status_code == 402
        response = post(client, f'/notes/{down.pk}/links', auth, {})
        assert response.status_code == 409
        assert response.json()['detail'] == 'This note was taken down and cannot be shared.'

    def test_list_active_first(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')
        old = links.create_link(note, mode='read', label='old')
        links.revoke_link(old)
        new = links.create_link(note, mode='edit', label='new')

        body = get(client, f'/notes/{note.pk}/links', auth).json()

        assert [item['label'] for item in body] == ['new', 'old']
        assert [item['active'] for item in body] == [True, False]
        assert body[1]['revoked_at'] is not None and body[0]['id'] == str(new.pk)

    def test_another_users_links_are_a_404(self, client, auth, other_user):
        note = create_note(other_user, title='T')
        link = links.create_link(note, mode='read')

        assert get(client, f'/notes/{note.pk}/links', auth).status_code == 404
        assert post(client, f'/notes/{note.pk}/links', auth, {}).status_code == 404
        assert (
            patch(client, f'/notes/{note.pk}/links/{link.pk}', auth, {'label': 'x'}).status_code
            == 404
        )
        assert delete(client, f'/notes/{note.pk}/links/{link.pk}', auth).status_code == 404

    def test_a_link_of_another_note_is_a_404(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')
        other = create_note(funded_user, title='Other')
        link = links.create_link(other, mode='read')

        assert (
            patch(client, f'/notes/{note.pk}/links/{link.pk}', auth, {'label': 'x'}).status_code
            == 404
        )
        assert delete(client, f'/notes/{note.pk}/links/{link.pk}', auth).status_code == 404

    def test_update_mode_label_and_expiry(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')
        link = links.create_link(note, mode='read', label='a')
        day = (timezone.now() + timedelta(days=9)).date().isoformat()

        body = patch(
            client,
            f'/notes/{note.pk}/links/{link.pk}',
            auth,
            {'mode': 'edit', 'label': 'b', 'expires_at': day},
        ).json()

        assert (body['mode'], body['label']) == ('edit', 'b') and body['expires_at'] is not None

        cleared = patch(
            client, f'/notes/{note.pk}/links/{link.pk}', auth, {'label': None, 'expires_at': None}
        ).json()
        assert cleared['label'] == '' and cleared['expires_at'] is None
        assert cleared['mode'] == 'edit'

    def test_a_mode_cannot_be_null_and_an_empty_patch_spends_nothing(
        self, client, auth, funded_user, settings
    ):
        note = create_note(funded_user, title='T')
        link = links.create_link(note, mode='read')
        limit_rule(settings, 'NOTES_EDIT_USER', limit=1)

        for _ in range(3):
            assert patch(client, f'/notes/{note.pk}/links/{link.pk}', auth, {}).status_code == 200
            response = patch(client, f'/notes/{note.pk}/links/{link.pk}', auth, {'mode': None})
            assert response.status_code == 400
            assert response.json()['detail'] == 'mode: Cannot be null.'

    def test_extending_an_expired_link_makes_it_active_again(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')
        link = make_link(note, expires_at=timezone.now() - timedelta(days=1))
        day = (timezone.now() + timedelta(days=5)).date().isoformat()

        body = patch(client, f'/notes/{note.pk}/links/{link.pk}', auth, {'expires_at': day}).json()

        assert body['active'] is True

    def test_a_revoked_link_cannot_be_changed(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')
        link = links.create_link(note, mode='read')
        links.revoke_link(link)

        response = patch(client, f'/notes/{note.pk}/links/{link.pk}', auth, {'label': 'x'})

        assert response.status_code == 400
        assert response.json()['detail'] == 'This link was revoked and can no longer be changed.'

    def test_revoke_is_idempotent_final_and_allowed_on_a_read_only_note(
        self, client, auth, funded_user
    ):
        note = create_note(funded_user, title='T')
        link = links.create_link(note, mode='read')
        Note.objects.filter(pk=note.pk).update(locked_at=timezone.now())

        first = delete(client, f'/notes/{note.pk}/links/{link.pk}', auth)
        second = delete(client, f'/notes/{note.pk}/links/{link.pk}', auth)

        assert first.status_code == second.status_code == 204
        assert ShareLink.objects.get(pk=link.pk).revoked_at is not None
        assert get(client, f'/notes/{note.pk}/links', auth).json()[0]['active'] is False

    def test_the_edit_budget(self, client, auth, funded_user, settings):
        note = create_note(funded_user, title='T')
        limit_rule(settings, 'NOTES_EDIT_USER', limit=1)

        assert post(client, f'/notes/{note.pk}/links', auth, {}).status_code == 201
        assert post(client, f'/notes/{note.pk}/links', auth, {}).status_code == 429

    def test_has_active_link_follows_the_links(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')
        assert get(client, f'/notes/{note.pk}', auth).json()['has_active_link'] is False
        link = links.create_link(note, mode='read')
        assert get(client, f'/notes/{note.pk}', auth).json()['has_active_link'] is True
        links.revoke_link(link)
        assert get(client, f'/notes/{note.pk}', auth).json()['has_active_link'] is False
