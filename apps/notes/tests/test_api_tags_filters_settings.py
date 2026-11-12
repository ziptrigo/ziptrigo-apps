"""The Notes JWT API's tag, saved-filter, settings and shared-note endpoints."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from ..models import DEFAULT_TAG_COLOR, Note, NotesSettings, SavedFilter, Tag
from ..services import create_note, links, takedown, trash
from .api_support import (  # noqa: F401 -- the fixtures are used by name
    auth,
    bearer,
    delete,
    get,
    limit_rule,
    other_auth,
    patch,
    post,
)
from .conftest import balance
from .factories import make_link, make_note, make_tag, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

TAG_KEYS = {'id', 'name', 'color', 'description', 'position', 'note_count'}
FILTER_KEYS = {'id', 'name', 'position', 'params', 'summary', 'created_at'}
PARAM_KEYS = {
    'q',
    'mode',
    'scope',
    'tags',
    'shared',
    'updated',
    'updated_from',
    'updated_to',
    'sort',
    'dir',
}


def names(response) -> list[str]:
    return [item['name'] for item in response.json()]


def titles(response) -> list[str]:
    return [note['title'] for note in response.json()['results']]


class TestTags:
    def test_list_in_order_with_note_counts(self, client, auth, funded_user):
        home = make_tag(funded_user, 'Home', position=1)
        work = make_tag(funded_user, 'Work', position=0)
        create_note(funded_user, title='a', tags=[home.pk])
        trash(create_note(funded_user, title='b', tags=[home.pk]))  # the Trash doesn't count

        response = get(client, '/tags', auth)

        assert response.status_code == 200
        assert names(response) == ['Work', 'Home']
        assert set(response.json()[0]) == TAG_KEYS
        assert [tag['note_count'] for tag in response.json()] == [0, 1]
        assert work.pk

    def test_only_the_callers_tags(self, client, auth, other_user):
        make_tag(other_user, 'Secret')

        assert get(client, '/tags', auth).json() == []

    def test_create_appends(self, client, auth, funded_user):
        make_tag(funded_user, 'First', position=3)

        response = post(
            client,
            '/tags',
            auth,
            {'name': '  Big   idea ', 'color': '#ABCDEF', 'description': 'for  later'},
        )

        assert response.status_code == 201
        body = response.json()
        assert set(body) == TAG_KEYS
        assert body['name'] == 'Big idea' and body['color'] == '#abcdef'
        assert body['description'] == 'for later' and body['position'] == 4
        assert body['note_count'] == 0

    def test_the_default_colour(self, client, auth):
        assert post(client, '/tags', auth, {'name': 'x'}).json()['color'] == DEFAULT_TAG_COLOR

    @pytest.mark.parametrize(
        ('payload', 'message'),
        [
            ({'name': ' '}, 'name: Name is required.'),
            ({'name': 'x' * 51}, 'name: Name is too long'),
            ({'name': 'a', 'color': 'red'}, 'color: Use a colour like #8fa89e.'),
            ({'name': 'a', 'description': 'x' * 256}, 'description: Description is too long'),
        ],
    )
    def test_a_refusal_is_a_400_and_spends_no_budget(
        self, client, auth, settings, payload, message
    ):
        limit_rule(settings, 'NOTES_EDIT_USER', limit=1)

        for _ in range(3):
            response = post(client, '/tags', auth, payload)
            assert response.status_code == 400
            assert message in response.json()['detail']
        assert post(client, '/tags', auth, {'name': 'fine'}).status_code == 201

    def test_names_are_unique_ignoring_case(self, client, auth, funded_user):
        make_tag(funded_user, 'Work')

        response = post(client, '/tags', auth, {'name': 'WORK'})

        assert response.status_code == 400
        assert response.json()['detail'] == 'name: You already have a tag with this name.'

    def test_the_cap_on_tags(self, client, auth, funded_user):
        row = NotesSettings.load()
        row.max_tags_per_user = 1
        row.save()
        make_tag(funded_user, 'One')

        response = post(client, '/tags', auth, {'name': 'Two'})

        assert response.status_code == 400
        assert response.json()['detail'] == 'You have too many tags.'

    def test_patch_only_the_fields_present(self, client, auth, funded_user):
        tag = make_tag(funded_user, 'Work', color='#112233', description='old')

        body = patch(client, f'/tags/{tag.pk}', auth, {'name': 'Job'}).json()
        assert (body['name'], body['color'], body['description']) == ('Job', '#112233', 'old')

        cleared = patch(
            client, f'/tags/{tag.pk}', auth, {'description': None, 'color': None}
        ).json()
        assert (cleared['description'], cleared['color']) == ('', DEFAULT_TAG_COLOR)

    def test_patch_refusals(self, client, auth, funded_user):
        make_tag(funded_user, 'Work')
        other = make_tag(funded_user, 'Home')

        assert patch(client, f'/tags/{other.pk}', auth, {'name': 'work'}).status_code == 400
        assert patch(client, f'/tags/{other.pk}', auth, {'name': None}).status_code == 400
        assert patch(client, f'/tags/{other.pk}', auth, {'size': 3}).status_code == 422
        assert patch(client, f'/tags/{other.pk}', auth, {}).status_code == 200

    def test_delete_keeps_the_notes_other_tags(self, client, auth, funded_user):
        gone, kept = make_tag(funded_user, 'Gone'), make_tag(funded_user, 'Kept')
        note = create_note(funded_user, title='a', tags=[gone.pk, kept.pk])

        assert delete(client, f'/tags/{gone.pk}', auth).status_code == 204

        assert [tag.name for tag in Note.objects.get(pk=note.pk).tags.all()] == ['Kept']
        assert get(client, f'/notes/{note.pk}', auth).json()['tags'][0]['name'] == 'Kept'

    def test_reorder(self, client, auth, funded_user, other_user):
        first, second = (
            make_tag(funded_user, 'A', position=0),
            make_tag(funded_user, 'B', position=1),
        )
        foreign = make_tag(other_user, 'C')

        response = post(
            client,
            '/tags/reorder',
            auth,
            {'ids': [str(second.pk), str(foreign.pk), str(first.pk)]},
        )

        assert response.status_code == 200 and names(response) == ['B', 'A']
        assert Tag.objects.get(pk=foreign.pk).position == 0  # untouched
        too_many = {'ids': [str(uuid.uuid4()) for _ in range(501)]}
        assert post(client, '/tags/reorder', auth, too_many).status_code == 422

    def test_another_users_tag_is_a_404(self, client, auth, other_user):
        tag = make_tag(other_user, 'Secret')

        assert patch(client, f'/tags/{tag.pk}', auth, {'name': 'x'}).status_code == 404
        assert delete(client, f'/tags/{tag.pk}', auth).status_code == 404


class TestSettings:
    def test_the_prices_and_the_free_slots(self, client, auth, funded_user, config):
        for index in range(2):
            create_note(funded_user, title=f'n{index}')

        body = get(client, '/settings', auth).json()

        assert body == {
            'free_notes': 5,
            'free_notes_used': 2,
            'creation_price': 3,
            'renewal_price': 5,
            'renewal_days': 365,
            'next_note_cost': 0,
            'trash_days': 30,
        }

    def test_the_next_note_costs_when_the_slots_are_full(self, client, auth, funded_user):
        notes = [create_note(funded_user, title=f'n{index}') for index in range(5)]

        assert get(client, '/settings', auth).json()['next_note_cost'] == 3
        trash(notes[0])
        body = get(client, '/settings', auth).json()
        assert (body['free_notes_used'], body['next_note_cost']) == (4, 0)

    def test_it_needs_a_token(self, client):
        assert client.get('/api/notes/settings').status_code == 401


class TestFilters:
    def test_create_list_and_summary(self, client, auth, funded_user):
        tag = make_tag(funded_user, 'Work')

        response = post(
            client,
            '/filters',
            auth,
            {
                'name': 'Deploys',
                'params': {
                    'q': 'deploy*',
                    'tags': [str(tag.pk)],
                    'shared': 'yes',
                    'updated': 'last_7_days',
                    'sort': 'title',
                    'dir': 'desc',
                },
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert set(body) == FILTER_KEYS and set(body['params']) == PARAM_KEYS
        assert body['params']['tags'] == [str(tag.pk)]
        assert (body['params']['shared'], body['params']['dir']) == ('yes', 'desc')
        assert body['summary'] == '@Work · shared · last 7 days · "deploy*" · by title (Z to A)'
        assert names(get(client, '/filters', auth)) == ['Deploys']

    def test_the_default_params(self, client, auth):
        body = post(client, '/filters', auth, {'name': 'Everything'}).json()

        assert body['params'] == {
            'q': '',
            'mode': 'glob',
            'scope': 'active',
            'tags': [],
            'shared': None,
            'updated': 'any',
            'updated_from': None,
            'updated_to': None,
            'sort': 'updated',
            'dir': None,
        }
        assert body['summary'] == 'all notes'

    @pytest.mark.parametrize(
        ('payload', 'message'),
        [
            ({'name': ' '}, 'name: Name is required.'),
            ({'name': 'x', 'params': {'q': '/(/'}}, 'Invalid regular expression.'),
            ({'name': 'x', 'params': {'q': 'a' * 201}}, 'The pattern is too long.'),
        ],
    )
    def test_a_refusal_is_a_400_and_spends_no_budget(
        self, client, auth, settings, payload, message
    ):
        limit_rule(settings, 'NOTES_EDIT_USER', limit=1)

        for _ in range(3):
            response = post(client, '/filters', auth, payload)
            assert response.status_code == 400
            assert message in response.json()['detail']
        assert post(client, '/filters', auth, {'name': 'fine'}).status_code == 201

    @pytest.mark.parametrize(
        'params',
        [
            {'mode': 'fuzzy'},
            {'scope': 'trash'},
            {'sort': 'size'},
            {'dir': 'up'},
            {'shared': ''},
            {'updated': 'lately'},
            {'tags': ['work']},
            {'priority': 1},
        ],
    )
    def test_a_bad_param_is_a_422(self, client, auth, params):
        assert post(client, '/filters', auth, {'name': 'x', 'params': params}).status_code == 422

    def test_names_are_unique_and_capped(self, client, auth, funded_user):
        post(client, '/filters', auth, {'name': 'Work'})

        assert post(client, '/filters', auth, {'name': 'work'}).status_code == 400
        for index in range(49):
            SavedFilter.objects.create(user=funded_user, name=f'f{index}', position=index + 1)
        response = post(client, '/filters', auth, {'name': 'one more'})
        assert response.status_code == 400
        assert response.json()['detail'] == 'You can have up to 50 saved filters.'

    def test_patch_renames_and_replaces_the_params(self, client, auth, funded_user):
        saved = post(client, '/filters', auth, {'name': 'Old', 'params': {'q': 'a'}}).json()

        renamed = patch(client, f'/filters/{saved["id"]}', auth, {'name': 'New'}).json()
        assert (renamed['name'], renamed['params']['q']) == ('New', 'a')

        replaced = patch(
            client, f'/filters/{saved["id"]}', auth, {'params': {'scope': 'all', 'sort': 'created'}}
        ).json()
        assert replaced['params']['q'] == '' and replaced['params']['scope'] == 'all'
        assert replaced['name'] == 'New'

    def test_patch_cannot_clear_and_a_refusal_changes_nothing(self, client, auth):
        saved = post(client, '/filters', auth, {'name': 'Keep', 'params': {'q': 'a'}}).json()
        url = f'/filters/{saved["id"]}'

        assert patch(client, url, auth, {'name': None}).json()['detail'] == 'name: Cannot be null.'
        assert patch(client, url, auth, {'params': None}).status_code == 400
        bad = patch(client, url, auth, {'name': 'Renamed', 'params': {'q': '/(/'}})
        assert bad.status_code == 400
        assert patch(client, url, auth, {}).json()['name'] == 'Keep'
        assert patch(client, url, auth, {'name': 'Keep', 'extra': 1}).status_code == 422

    def test_patch_empty_spends_nothing(self, client, auth, settings):
        saved = post(client, '/filters', auth, {'name': 'a'}).json()
        limit_rule(settings, 'NOTES_EDIT_USER', limit=1)

        for _ in range(3):
            assert patch(client, f'/filters/{saved["id"]}', auth, {}).status_code == 200

    def test_a_deleted_tag_drops_out_of_the_params(self, client, auth, funded_user):
        tag = make_tag(funded_user, 'Work')
        saved = post(
            client, '/filters', auth, {'name': 'W', 'params': {'tags': [str(tag.pk), 'none']}}
        ).json()
        tag.delete()

        body = get(client, '/filters', auth).json()[0]

        assert body['id'] == saved['id'] and body['params']['tags'] == ['none']
        assert body['summary'] == 'no tag'

    def test_delete_and_reorder(self, client, auth):
        first = post(client, '/filters', auth, {'name': 'A'}).json()
        second = post(client, '/filters', auth, {'name': 'B'}).json()

        reordered = post(
            client,
            '/filters/reorder',
            auth,
            {'ids': [second['id'], first['id'], str(uuid.uuid4())]},
        )
        assert reordered.status_code == 200 and names(reordered) == ['B', 'A']
        assert delete(client, f'/filters/{first["id"]}', auth).status_code == 204
        assert names(get(client, '/filters', auth)) == ['B']
        too_many = {'ids': [str(uuid.uuid4()) for _ in range(51)]}
        assert post(client, '/filters/reorder', auth, too_many).status_code == 422

    def test_another_users_filter_is_a_404(self, client, auth, other_user):
        saved = SavedFilter.objects.create(user=other_user, name='Secret', params={})

        assert patch(client, f'/filters/{saved.pk}', auth, {'name': 'x'}).status_code == 404
        assert delete(client, f'/filters/{saved.pk}', auth).status_code == 404
        assert get(client, f'/filters/{saved.pk}/notes', auth).status_code == 404

    def test_run_a_filter(self, client, auth, funded_user):
        tag = make_tag(funded_user, 'Work')
        create_note(funded_user, title='Deploy plan', content='x', tags=[tag.pk])
        create_note(funded_user, title='Deploy untagged', content='x')
        create_note(funded_user, title='Other', tags=[tag.pk])
        saved = post(
            client,
            '/filters',
            auth,
            {'name': 'W', 'params': {'q': 'deploy', 'tags': [str(tag.pk)], 'sort': 'title'}},
        ).json()

        response = get(client, f'/filters/{saved["id"]}/notes', auth)

        assert response.status_code == 200
        body = response.json()
        assert (body['count'], body['limit'], body['offset']) == (1, 50, 0)
        item = body['results'][0]
        assert item['title'] == 'Deploy plan' and item['search_field'] == 'title'
        assert 'content' not in item

    def test_running_a_filter_honours_scope_sort_and_dir_and_never_lists_the_trash(
        self, client, auth, funded_user
    ):
        from ..services import archive

        for name in ('a', 'b', 'c'):
            create_note(funded_user, title=name)
        archive(Note.objects.get(title='a'))
        trash(Note.objects.get(title='c'))
        every = post(
            client,
            '/filters',
            auth,
            {'name': 'all', 'params': {'scope': 'all', 'sort': 'title', 'dir': 'desc'}},
        ).json()
        archived = post(
            client, '/filters', auth, {'name': 'arch', 'params': {'scope': 'archived'}}
        ).json()

        assert titles(get(client, f'/filters/{every["id"]}/notes', auth)) == ['b', 'a']
        assert titles(get(client, f'/filters/{archived["id"]}/notes', auth)) == ['a']

    def test_running_pages_and_spends_the_search_budget(self, client, auth, funded_user, settings):
        for index in range(3):
            make_note(funded_user, title=f'n{index}', position=index)
        saved = post(client, '/filters', auth, {'name': 'x', 'params': {'sort': 'manual'}}).json()
        url = f'/filters/{saved["id"]}/notes'
        limit_rule(settings, 'NOTES_SEARCH_USER', limit=1)

        page = get(client, url, auth, limit=2, offset=1)
        assert page.status_code == 200 and titles(page) == ['n1', 'n2']
        assert page.json()['count'] == 3
        assert get(client, url, auth).status_code == 429

    def test_a_filter_with_a_search_that_cannot_run_is_a_400(self, client, auth, funded_user):
        saved = SavedFilter.objects.create(
            user=funded_user, name='broken', params={'q': '/(/', 'mode': 'glob'}
        )

        assert get(client, f'/filters/{saved.pk}/notes', auth).status_code == 400

    def test_a_saved_search_that_cannot_run_spends_nothing(
        self, client, auth, funded_user, settings, ratelimit_clock
    ):
        broken = SavedFilter.objects.create(
            user=funded_user, name='broken', params={'q': '/(/', 'mode': 'glob'}
        )
        fine = SavedFilter.objects.create(user=funded_user, name='fine', params={'q': 'x'})
        limit_rule(settings, 'NOTES_SEARCH_USER', limit=1)

        for _ in range(3):
            assert get(client, f'/filters/{broken.pk}/notes', auth).status_code == 400
        assert get(client, f'/filters/{fine.pk}/notes', auth).status_code == 200
        assert get(client, f'/filters/{fine.pk}/notes', auth).status_code == 429

    def test_the_edit_budget(self, client, auth, settings):
        limit_rule(settings, 'NOTES_EDIT_USER', limit=1)

        assert post(client, '/filters', auth, {'name': 'a'}).status_code == 201
        assert post(client, '/filters', auth, {'name': 'b'}).status_code == 429


class TestUnknownKeysInOrderBodies:
    def test_reorder_bodies_forbid_extra_keys(self, client, auth):
        for path in ('/tags/reorder', '/filters/reorder'):
            response = post(client, path, auth, {'ids': [], 'extra': 1})
            assert response.status_code == 422
            assert post(client, path, auth, {'ids': []}).status_code == 200


class TestSharedDuplicate:
    def test_a_note_that_stops_working_during_the_copy_is_the_same_404(
        self, client, other_auth, funded_user, other_user, monkeypatch
    ):
        """The link is looked up, then the note is taken down / trashed / locked / unshared before
        the copy is made: the answer is the neutral 404, never the takedown's 409."""
        from apps.notes.api import shared

        def stale_lookup(link):
            monkeypatch.setattr(shared.links, 'get_available', lambda code: link)

        unknown = post(client, '/shared/nope/duplicate', other_auth)
        stoppers = {
            'taken down': lambda note, link: takedown.take_down_note(
                note, by=make_user(is_staff=True), reason='x'
            ),
            'trashed': lambda note, link: trash(note),
            'locked': lambda note, link: Note.objects.filter(pk=note.pk).update(
                locked_at=timezone.now()
            ),
            'revoked': lambda note, link: links.revoke_link(link),
        }
        for name, stop in stoppers.items():
            note = create_note(funded_user, title=name)
            link = links.create_link(note, mode='read')
            held = links.get_available(link.code)
            assert held is not None
            stale_lookup(held)  # the endpoint's own lookup saw it working
            stop(note, link)

            response = post(client, f'/shared/{link.code}/duplicate', other_auth)

            assert response.status_code == 404, name
            assert response.content == unknown.content, name
        assert not Note.objects.filter(user=other_user).exists()

    def test_copy_a_shared_note(self, client, other_auth, funded_user, other_user):
        # `other_user` copies a note `funded_user` shared.
        note = create_note(
            funded_user, title='Recipe', content='eggs', tags=[make_tag(funded_user, 'T').pk]
        )
        link = links.create_link(note, mode='read')

        response = post(client, f'/shared/{link.code}/duplicate', other_auth)

        assert response.status_code == 201
        body = response.json()
        assert (body['title'], body['content'], body['tags']) == ('Recipe', 'eggs', [])
        copy = Note.objects.get(pk=body['id'])
        assert copy.user == other_user and copy.pk != note.pk
        assert body['has_active_link'] is False

    def test_every_unavailable_code_is_the_same_404(
        self, client, other_auth, funded_user, other_user
    ):
        note = create_note(funded_user, title='T')
        revoked = links.create_link(note, mode='read')
        links.revoke_link(revoked)
        expired = make_link(note, expires_at=timezone.now() - timedelta(days=1))
        down = create_note(funded_user, title='D')
        down_link = links.create_link(down, mode='read')
        takedown.take_down_note(down, by=make_user(is_staff=True), reason='x')
        trashed = create_note(funded_user, title='Tr')
        trashed_link = links.create_link(trashed, mode='read')
        trash(trashed)
        locked = make_note(funded_user, title='L', locked_at=timezone.now())
        locked_link = make_link(locked)

        responses = [
            post(client, f'/shared/{code}/duplicate', other_auth)
            for code in (
                revoked.code,
                expired.code,
                down_link.code,
                trashed_link.code,
                locked_link.code,
                'nope',
                'x' * 100,
            )
        ]

        assert {response.status_code for response in responses} == {404}
        assert len({response.content for response in responses}) == 1
        assert not Note.objects.filter(user=other_user).exists()

    def test_cost_and_expected_cost(self, client, auth, other_auth, funded_user, other_user):
        from apps.billing.services import add_credits

        add_credits(other_user, 100, description='t', source='test')
        note = create_note(funded_user, title='Recipe')
        link = links.create_link(note, mode='read')
        for index in range(5):
            create_note(other_user, title=f'mine {index}')

        wrong = post(client, f'/shared/{link.code}/duplicate', other_auth, expected_cost=0)
        right = post(client, f'/shared/{link.code}/duplicate', other_auth, expected_cost=3)

        assert wrong.status_code == 409 and 'expected_cost=3' in wrong.json()['detail']
        assert right.status_code == 201
        assert right.json()['charged_credits'] == 3 and balance(other_user) == 97

    def test_insufficient_credits_is_a_402(self, client, other_auth, funded_user, other_user):
        note = create_note(funded_user, title='Recipe')
        link = links.create_link(note, mode='read')
        for index in range(5):
            create_note(other_user, title=f'mine {index}')

        response = post(client, f'/shared/{link.code}/duplicate', other_auth)

        assert response.status_code == 402
        assert Note.objects.filter(user=other_user).count() == 5

    def test_the_owner_can_copy_their_own_link_and_it_needs_a_token(
        self, client, auth, funded_user
    ):
        note = create_note(funded_user, title='T')
        link = links.create_link(note, mode='read')

        assert post(client, f'/shared/{link.code}/duplicate', auth).status_code == 201
        anonymous = client.post(f'/api/notes/shared/{link.code}/duplicate')
        assert anonymous.status_code == 401

    def test_the_create_budget(self, client, other_auth, funded_user, settings):
        note = create_note(funded_user, title='T')
        link = links.create_link(note, mode='read')
        limit_rule(settings, 'NOTES_CREATE_USER', limit=1)

        assert post(client, f'/shared/{link.code}/duplicate', other_auth).status_code == 201
        assert post(client, f'/shared/{link.code}/duplicate', other_auth).status_code == 429
