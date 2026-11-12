"""The Notes JWT API's note endpoints (`/api/notes/notes`)."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from ..models import Note, NotesSettings, NoteVersion
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

NOTE_KEYS = {
    'id',
    'title',
    'content',
    'content_html',
    'version',
    'tags',
    'archived_at',
    'deleted_at',
    'is_free',
    'paid_through',
    'locked',
    'taken_down',
    'takedown_reason',
    'has_active_link',
    'charged_credits',
    'restore_cost',
    'created_at',
    'updated_at',
}
ITEM_KEYS = (NOTE_KEYS - {'content', 'content_html'}) | {
    'excerpt',
    'search_field',
    'search_snippet',
    'match_start',
    'match_end',
}
LOCKED = 'This note is locked until its renewal is paid.'
TAKEN_DOWN = 'This note was taken down and can no longer be changed.'
IN_TRASH = 'This note is in the Trash.'


def titles(response) -> list[str]:
    return [note['title'] for note in response.json()['results']]


def fill_free_slots(user, count: int = 5) -> list[Note]:
    return [create_note(user, title=f'Free {index}') for index in range(count)]


def stale(client, auth, note, **payload):
    """A PATCH on version 1 after the note was moved on to version 2."""
    return patch(client, f'/notes/{note.pk}', auth, {'base_version': 1, **payload})


class TestAuthentication:
    def test_no_token_is_401(self, client):
        assert client.get('/api/notes/notes').status_code == 401
        assert (
            client.post('/api/notes/notes', data='{}', content_type='application/json').status_code
            == 401
        )


class TestList:
    def test_items_have_an_excerpt_and_no_content(self, client, auth, funded_user):
        tag = make_tag(funded_user, 'Work', color='#112233')
        create_note(
            funded_user, title='A', content='# Heading\n\nSome **bold** text', tags=[tag.pk]
        )

        response = get(client, '/notes', auth)

        assert response.status_code == 200
        body = response.json()
        assert (body['count'], body['limit'], body['offset']) == (1, 50, 0)
        item = body['results'][0]
        assert set(item) == ITEM_KEYS
        assert 'content' not in item
        assert item['excerpt'] == 'Heading Some bold text'
        assert item['tags'] == [{'id': str(tag.pk), 'name': 'Work', 'color': '#112233'}]
        assert item['restore_cost'] is None and item['has_active_link'] is False
        assert item['search_field'] is None and item['search_snippet'] is None

    def test_the_excerpt_is_cut_at_140_characters(self, client, auth, funded_user):
        create_note(funded_user, title='Long', content='word ' * 100)

        excerpt = get(client, '/notes', auth).json()['results'][0]['excerpt']

        assert len(excerpt) == 140 and excerpt.endswith('…')

    def test_only_the_callers_notes(self, client, auth, other_user):
        create_note(other_user, title='Secret')

        assert get(client, '/notes', auth).json()['count'] == 0

    def test_views(self, client, auth, funded_user):
        from ..services import archive

        create_note(funded_user, title='Active')
        archive(create_note(funded_user, title='Archived'))
        trash(create_note(funded_user, title='Trashed'))

        assert titles(get(client, '/notes', auth)) == ['Active']
        assert titles(get(client, '/notes', auth, view='active')) == ['Active']
        assert titles(get(client, '/notes', auth, view='archived')) == ['Archived']
        assert titles(get(client, '/notes', auth, view='trash')) == ['Trashed']
        assert sorted(titles(get(client, '/notes', auth, view='all'))) == ['Active', 'Archived']

    def test_the_default_order_is_the_manual_one_and_a_new_note_is_first(
        self, client, auth, funded_user
    ):
        create_note(funded_user, title='First made')
        create_note(funded_user, title='Second made')

        assert titles(get(client, '/notes', auth)) == ['Second made', 'First made']
        assert titles(get(client, '/notes', auth, dir='desc')) == ['First made', 'Second made']

    def test_sorts_and_directions(self, client, auth, funded_user):
        from ..services import save_note
        from ..services.versions import Editor

        for name in ('b', 'c', 'a'):
            create_note(funded_user, title=name)
        oldest = Note.objects.get(title='b')
        save_note(oldest, editor=Editor.for_user(oldest, funded_user), content='x', base_version=1)

        assert titles(get(client, '/notes', auth, sort='title')) == ['a', 'b', 'c']
        assert titles(get(client, '/notes', auth, sort='title', dir='desc')) == ['c', 'b', 'a']
        assert titles(get(client, '/notes', auth, sort='updated'))[0] == 'b'
        assert titles(get(client, '/notes', auth, sort='updated', dir='asc'))[-1] == 'b'
        assert titles(get(client, '/notes', auth, sort='created'))[0] == 'a'
        # `dir` alone reverses the default sort of the view (the archive's default is `updated`).
        assert titles(get(client, '/notes', auth, view='all'))[0] == 'b'
        assert titles(get(client, '/notes', auth, view='all', dir='asc'))[-1] == 'b'

    def test_the_trash_lists_the_latest_deleted_first_and_has_no_bare_dir(
        self, client, auth, funded_user
    ):
        first = create_note(funded_user, title='first')
        second = create_note(funded_user, title='second')
        Note.objects.filter(pk=first.pk).update(deleted_at=timezone.now() - timedelta(days=2))
        Note.objects.filter(pk=second.pk).update(deleted_at=timezone.now() - timedelta(days=1))

        assert titles(get(client, '/notes', auth, view='trash')) == ['second', 'first']
        response = get(client, '/notes', auth, view='trash', dir='asc')
        assert response.status_code == 400
        assert response.json()['detail'] == 'dir: Choose a sort to reverse.'
        assert titles(get(client, '/notes', auth, view='trash', sort='title', dir='desc')) == [
            'second',
            'first',
        ]

    @pytest.mark.parametrize(
        ('query', 'message'),
        [
            ({'view': 'everything'}, 'view must be one of'),
            ({'sort': 'size'}, 'sort must be one of'),
            ({'dir': 'sideways'}, 'dir must be one of'),
            ({'mode': 'fuzzy'}, 'mode must be one of'),
            ({'shared': 'maybe'}, 'shared must be one of'),
            ({'updated': 'lately'}, 'updated must be one of'),
            ({'updated_from': '10/11/2026'}, 'updated_from must be a date'),
            ({'tag': 'work'}, 'tag must be a comma-separated list of tag ids'),
        ],
    )
    def test_a_bad_query_value_is_a_400(self, client, auth, query, message):
        response = get(client, '/notes', auth, **query)

        assert response.status_code == 400
        assert message in response.json()['detail']

    def test_tag_filter(self, client, auth, funded_user):
        work, home = make_tag(funded_user, 'Work'), make_tag(funded_user, 'Home')
        create_note(funded_user, title='w', tags=[work.pk])
        create_note(funded_user, title='h', tags=[home.pk])
        create_note(funded_user, title='none')

        assert titles(get(client, '/notes', auth, tag=str(work.pk))) == ['w']
        assert sorted(titles(get(client, '/notes', auth, tag=f'{work.pk},{home.pk}'))) == ['h', 'w']
        assert titles(get(client, '/notes', auth, tag='none')) == ['none']
        assert get(client, '/notes', auth, tag=str(uuid.uuid4())).json()['count'] == 0

    def test_shared_filter(self, client, auth, funded_user):
        shared = create_note(funded_user, title='shared')
        create_note(funded_user, title='private')
        links.create_link(shared, mode='read')

        assert titles(get(client, '/notes', auth, shared='yes')) == ['shared']
        assert titles(get(client, '/notes', auth, shared='no')) == ['private']
        item = get(client, '/notes', auth, shared='yes').json()['results'][0]
        assert item['has_active_link'] is True

    def test_updated_ranges(self, client, auth, funded_user):
        old = create_note(funded_user, title='old')
        create_note(funded_user, title='new')
        Note.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(days=40))
        old_day = (timezone.now() - timedelta(days=40)).date().isoformat()

        assert titles(get(client, '/notes', auth, updated='today')) == ['new']
        assert titles(get(client, '/notes', auth, updated='last_30_days')) == ['new']
        assert titles(get(client, '/notes', auth, updated_to=old_day)) == ['old']
        both = get(client, '/notes', auth, updated='custom', updated_from=old_day)
        assert sorted(titles(both)) == ['new', 'old']

    def test_paging(self, client, auth, funded_user):
        for index in range(5):
            make_note(funded_user, title=f'n{index}', position=index)

        page = get(client, '/notes', auth, limit=2, offset=1).json()

        assert (page['count'], page['limit'], page['offset']) == (5, 2, 1)
        assert titles(get(client, '/notes', auth, limit=2, offset=1)) == ['n1', 'n2']
        assert get(client, '/notes', auth, limit=500).json()['limit'] == 100
        assert get(client, '/notes', auth, limit=0).json()['limit'] == 1

    def test_a_search_finds_title_and_content_with_a_snippet(self, client, auth, funded_user):
        create_note(funded_user, title='Deploy checklist', content='unrelated')
        create_note(funded_user, title='Other', content='remember to rotate the keys soon')
        create_note(funded_user, title='Nothing', content='zzz')

        response = get(client, '/notes', auth, q='deploy')
        by_title = {item['title']: item for item in response.json()['results']}
        assert list(by_title) == ['Deploy checklist']
        item = by_title['Deploy checklist']
        assert item['search_field'] == 'title'
        snippet = item['search_snippet']
        assert snippet[item['match_start'] : item['match_end']] == 'deploy'

        content = get(client, '/notes', auth, q='rotate*keys').json()['results'][0]
        assert content['search_field'] == 'content'
        assert content['search_snippet'][content['match_start'] : content['match_end']] == (
            'rotate the keys'
        )

    def test_a_regex_and_the_slashes_form(self, client, auth, funded_user):
        create_note(funded_user, title='v1.2.3')
        create_note(funded_user, title='plain')

        assert titles(get(client, '/notes', auth, q=r'v\d+', mode='regex')) == ['v1.2.3']
        assert titles(get(client, '/notes', auth, q=r'/v\d+/')) == ['v1.2.3']

    def test_a_search_that_cannot_run_is_a_400(self, client, auth):
        assert get(client, '/notes', auth, q='/(/').status_code == 400
        assert get(client, '/notes', auth, q='x' * 201).status_code == 400

    def test_the_trash_can_be_searched_and_a_trashed_note_is_not_found_elsewhere(
        self, client, auth, funded_user
    ):
        trash(create_note(funded_user, title='needle in the trash'))
        create_note(funded_user, title='needle active')

        assert titles(get(client, '/notes', auth, q='needle')) == ['needle active']
        assert titles(get(client, '/notes', auth, q='needle', view='trash')) == [
            'needle in the trash'
        ]

    def test_trashed_items_carry_what_a_restore_costs(self, client, auth, funded_user, config):
        free = fill_free_slots(funded_user)[0]
        trash(free)  # frees its slot ...
        create_note(funded_user, title='Takes the slot')  # ... which another note takes
        due = make_note(
            funded_user,
            title='due',
            is_free=False,
            paid_through=timezone.now() - timedelta(days=1),
            deleted_at=timezone.now(),
        )
        not_due = make_note(
            funded_user,
            title='not due',
            is_free=False,
            paid_through=timezone.now() + timedelta(days=30),
            deleted_at=timezone.now(),
        )

        costs = {
            item['title']: item['restore_cost']
            for item in get(client, '/notes', auth, view='trash').json()['results']
        }

        # A free note into full slots: the creation price; a paid one that fell due: the renewal.
        assert costs == {free.title: 3, due.title: 5, not_due.title: 0}

    def test_a_search_spends_the_search_budget_and_a_plain_list_does_not(
        self, client, auth, settings
    ):
        limit_rule(settings, 'NOTES_SEARCH_USER', limit=1)

        assert get(client, '/notes', auth).status_code == 200
        assert get(client, '/notes', auth).status_code == 200
        assert get(client, '/notes', auth, q='x').status_code == 200
        second = get(client, '/notes', auth, q='x')
        assert second.status_code == 429 and 'Retry-After' in second


class TestQueries:
    def test_a_list_of_100_notes_does_not_query_per_note(
        self, client, auth, funded_user, django_assert_num_queries
    ):
        tag = make_tag(funded_user, 'Work')
        for index in range(100):
            note = make_note(
                funded_user, title=f'n{index}', content=f'text {index}', position=index
            )
            note.tags.add(tag)
        make_link(Note.objects.first())

        # The token's user, the count, the page and the tags of the page: nothing per note.
        with django_assert_num_queries(4):
            response = get(client, '/notes', auth, limit=100)

        assert len(response.json()['results']) == 100

    def test_a_list_of_trashed_notes_reads_the_settings_once(
        self, client, auth, funded_user, django_assert_num_queries
    ):
        for index in range(30):
            make_note(funded_user, title=f'n{index}', deleted_at=timezone.now())

        with django_assert_num_queries(6):
            response = get(client, '/notes', auth, view='trash', limit=100)

        assert len(response.json()['results']) == 30


class TestCreate:
    def test_a_free_note(self, client, auth, funded_user):
        tag = make_tag(funded_user, 'Work')

        response = post(
            client,
            '/notes',
            auth,
            {'title': '  Plan  ', 'content': '# H\r\n\r\ntext', 'tags': [str(tag.pk)]},
        )

        assert response.status_code == 201
        body = response.json()
        assert set(body) == NOTE_KEYS
        assert body['title'] == 'Plan' and body['content'] == '# H\n\ntext'
        assert '<h1>' in body['content_html']
        assert body['version'] == 1 and body['is_free'] is True and body['charged_credits'] == 0
        assert body['paid_through'] is None and body['locked'] is False
        assert body['taken_down'] is False and body['takedown_reason'] is None
        assert body['tags'] == [{'id': str(tag.pk), 'name': 'Work', 'color': tag.color}]
        assert balance(funded_user) == 100
        version = NoteVersion.objects.get(note_id=body['id'])
        assert version.editor_kind == 'owner'

    def test_it_is_first_in_the_manual_order(self, client, auth, funded_user):
        post(client, '/notes', auth, {'title': 'one'})
        post(client, '/notes', auth, {'title': 'two'})

        assert titles(get(client, '/notes', auth)) == ['two', 'one']

    def test_beyond_the_free_slots_it_costs_credits(self, client, auth, funded_user):
        fill_free_slots(funded_user)

        response = post(client, '/notes', auth, {'title': 'Paid'})

        assert response.status_code == 201
        body = response.json()
        assert body['is_free'] is False and body['charged_credits'] == 3
        assert body['paid_through'] is not None
        assert balance(funded_user) == 97

    def test_expected_cost_is_checked_and_a_mismatch_charges_nothing(
        self, client, auth, funded_user
    ):
        fill_free_slots(funded_user)

        wrong = post(client, '/notes', auth, {'title': 'Paid', 'expected_cost': 0})
        right = post(client, '/notes', auth, {'title': 'Paid', 'expected_cost': 3})

        assert wrong.status_code == 409
        assert 'expected_cost=3' in wrong.json()['detail']
        assert right.status_code == 201
        assert Note.objects.filter(user=funded_user).count() == 6
        assert balance(funded_user) == 97

    def test_a_free_slot_with_a_nonzero_expectation_is_a_409(self, client, auth):
        response = post(client, '/notes', auth, {'title': 'Free', 'expected_cost': 3})

        assert response.status_code == 409
        assert 'now free' in response.json()['detail']

    def test_not_enough_credits_is_a_402_and_creates_nothing(self, client, funded_user, config):
        fill_free_slots(funded_user)
        from apps.billing.services import spend_credits

        spend_credits(funded_user, 99, source='test', description='drain')
        headers = bearer(funded_user)

        response = post(client, '/notes', headers, {'title': 'Paid'})

        assert response.status_code == 402
        assert Note.objects.filter(user=funded_user).count() == 5

    @pytest.mark.parametrize(
        ('payload', 'message'),
        [
            ({'title': '   '}, 'title: Give the note a title.'),
            ({'title': 'x' * 201}, 'title: Keep the title to 200 characters or fewer.'),
            ({'title': 'a', 'content': 'x' * 50_001}, 'content: Keep the note to 50,000'),
            ({'title': 'a', 'tags': [str(uuid.uuid4())]}, 'tags: Choose your own tags.'),
        ],
    )
    def test_a_refusal_is_a_400_and_spends_no_budget(
        self, client, auth, settings, payload, message
    ):
        limit_rule(settings, 'NOTES_CREATE_USER', limit=1)

        for _ in range(3):
            response = post(client, '/notes', auth, payload)
            assert response.status_code == 400
            assert message in response.json()['detail']

        assert post(client, '/notes', auth, {'title': 'fine'}).status_code == 201

    def test_the_cap_on_notes(self, client, auth, funded_user):
        row = NotesSettings.load()
        row.max_notes_per_user = 1
        row.save()
        post(client, '/notes', auth, {'title': 'one'})

        response = post(client, '/notes', auth, {'title': 'two'})

        assert response.status_code == 400
        assert response.json()['detail'] == 'You have too many notes. Delete some first.'

    def test_an_unknown_key_and_a_wrong_type_are_422(self, client, auth):
        unknown = post(client, '/notes', auth, {'title': 'x', 'content_html': '<b>'})
        wrong_type = post(client, '/notes', auth, {'title': 'x', 'tags': 'work'})
        missing = post(client, '/notes', auth, {'content': 'x'})

        assert unknown.status_code == wrong_type.status_code == missing.status_code == 422
        assert isinstance(unknown.json()['detail'], list)

    def test_the_create_budget(self, client, auth, settings):
        limit_rule(settings, 'NOTES_CREATE_USER', limit=1)

        assert post(client, '/notes', auth, {'title': 'a'}).status_code == 201
        response = post(client, '/notes', auth, {'title': 'b'})

        assert response.status_code == 429 and 'Retry-After' in response


class TestRead:
    def test_get_shows_content_and_the_rendered_html(self, client, auth, funded_user):
        note = create_note(funded_user, title='T', content='| a | b |\n|---|---|\n| 1 | 2 |\n')

        body = get(client, f'/notes/{note.pk}', auth).json()

        assert set(body) == NOTE_KEYS
        assert body['content'].startswith('| a | b |')
        assert '<table>' in body['content_html']

    def test_a_trashed_note_can_be_read_and_says_what_a_restore_costs(
        self, client, auth, funded_user
    ):
        note = create_note(funded_user, title='T')
        trash(note)

        body = get(client, f'/notes/{note.pk}', auth).json()

        assert body['deleted_at'] is not None and body['restore_cost'] == 0

    def test_another_users_note_is_a_404(self, client, auth, other_user):
        note = create_note(other_user, title='Secret')

        assert get(client, f'/notes/{note.pk}', auth).status_code == 404
        assert get(client, f'/notes/{uuid.uuid4()}', auth).status_code == 404
        assert get(client, '/notes/not-a-uuid', auth).status_code == 422

    def test_a_taken_down_note_shows_the_reason(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')
        takedown.take_down_note(
            note, by=make_user(is_staff=True), reason='Phishing', internal_note='secret'
        )

        response = get(client, f'/notes/{note.pk}', auth)

        body = response.json()
        assert body['taken_down'] is True and body['takedown_reason'] == 'Phishing'
        assert 'secret' not in response.content.decode()


class TestUpdate:
    def test_title_content_and_tags_with_the_version(self, client, auth, funded_user):
        note = create_note(funded_user, title='Old', content='old')
        tag = make_tag(funded_user, 'Work')

        response = patch(
            client,
            f'/notes/{note.pk}',
            auth,
            {'title': 'New', 'content': 'new', 'tags': [str(tag.pk)], 'base_version': 1},
        )

        assert response.status_code == 200
        body = response.json()
        assert (body['title'], body['content'], body['version']) == ('New', 'new', 2)
        assert [item['name'] for item in body['tags']] == ['Work']

    def test_a_save_is_recorded_as_a_version_by_the_owner(self, client, auth, funded_user):
        note = create_note(funded_user, title='Old', content='old')

        patch(client, f'/notes/{note.pk}', auth, {'content': 'new', 'base_version': 1})

        # Within ten minutes by the same editor the first version is overwritten in place.
        version = NoteVersion.objects.get(note=note, number=1)
        assert (version.content, version.editor_kind) == ('new', 'owner')

    def test_base_version_is_required_with_a_title_or_content(self, client, auth, funded_user):
        note = create_note(funded_user, title='Old')

        for payload in ({'title': 'New'}, {'content': 'x'}, {'content': None}):
            response = patch(client, f'/notes/{note.pk}', auth, payload)
            assert response.status_code == 400
            assert response.json()['detail'] == 'base_version: Send the version you edited.'

        note.refresh_from_db()
        assert note.title == 'Old'

    def test_tags_alone_need_no_version_and_do_not_move_updated_at(self, client, auth, funded_user):
        note = create_note(funded_user, title='Old')
        tag = make_tag(funded_user, 'Work')
        before = Note.objects.get(pk=note.pk).updated_at

        body = patch(client, f'/notes/{note.pk}', auth, {'tags': [str(tag.pk)]}).json()

        assert [item['name'] for item in body['tags']] == ['Work'] and body['version'] == 1
        assert Note.objects.get(pk=note.pk).updated_at == before

    def test_null_clears_content_and_tags_but_a_title_cannot_be_empty(
        self, client, auth, funded_user
    ):
        tag = make_tag(funded_user, 'Work')
        note = create_note(funded_user, title='T', content='text', tags=[tag.pk])

        body = patch(
            client, f'/notes/{note.pk}', auth, {'content': None, 'tags': None, 'base_version': 1}
        ).json()
        assert (body['content'], body['tags']) == ('', [])

        response = patch(client, f'/notes/{note.pk}', auth, {'title': None, 'base_version': 2})
        assert response.status_code == 400
        assert response.json()['detail'] == 'title: Give the note a title.'

    def test_a_stale_version_is_a_409_with_what_is_stored(self, client, auth, funded_user):
        note = create_note(funded_user, title='Mine', content='first')
        patch(client, f'/notes/{note.pk}', auth, {'content': 'second', 'base_version': 1})

        response = stale(client, auth, note, content='mine', title='Mine too')

        assert response.status_code == 409
        assert response.json() == {
            'detail': 'This note was changed by someone else while you were editing.',
            'current_version': 2,
            'current_title': 'Mine',
            'current_content': 'second',
        }
        assert Note.objects.get(pk=note.pk).content == 'second'

    def test_the_tags_are_not_changed_when_the_save_conflicts(self, client, auth, funded_user):
        note = create_note(funded_user, title='Mine', content='first')
        tag = make_tag(funded_user, 'Work')
        patch(client, f'/notes/{note.pk}', auth, {'content': 'second', 'base_version': 1})

        response = stale(client, auth, note, content='mine', tags=[str(tag.pk)])

        assert response.status_code == 409
        assert list(Note.objects.get(pk=note.pk).tags.all()) == []

    def test_changing_nothing_is_not_a_save_and_spends_no_budget(
        self, client, auth, funded_user, settings
    ):
        note = create_note(funded_user, title='Same', content='same')
        limit_rule(settings, 'NOTES_EDIT_USER', limit=1)

        for _ in range(3):
            assert patch(client, f'/notes/{note.pk}', auth, {}).status_code == 200
            assert patch(client, f'/notes/{note.pk}', auth, {'base_version': 1}).status_code == 200
        same = patch(
            client,
            f'/notes/{note.pk}',
            auth,
            {'title': 'Same', 'content': 'same', 'base_version': 9},
        )

        assert same.status_code == 200 and same.json()['version'] == 1

    @pytest.mark.parametrize(
        ('payload', 'message'),
        [
            ({'title': ' '}, 'title: Give the note a title.'),
            ({'content': 'x' * 50_001}, 'content: Keep the note to 50,000'),
            ({'tags': [str(uuid.uuid4())]}, 'tags: Choose your own tags.'),
        ],
    )
    def test_a_refusal_is_a_400_and_spends_no_budget(
        self, client, auth, funded_user, settings, payload, message
    ):
        note = create_note(funded_user, title='T')
        limit_rule(settings, 'NOTES_EDIT_USER', limit=1)

        for _ in range(3):
            response = patch(client, f'/notes/{note.pk}', auth, {'base_version': 1, **payload})
            assert response.status_code == 400
            assert message in response.json()['detail']

        ok = patch(client, f'/notes/{note.pk}', auth, {'base_version': 1, 'content': 'fine'})
        assert ok.status_code == 200

    def test_a_note_in_the_trash_is_a_400(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')
        trash(note)

        response = patch(client, f'/notes/{note.pk}', auth, {'title': 'x', 'base_version': 1})

        assert response.status_code == 400 and response.json()['detail'] == IN_TRASH

    def test_a_locked_note_is_a_402(self, client, auth, funded_user):
        note = make_note(funded_user, title='T', locked_at=timezone.now())

        for payload in ({'title': 'x', 'base_version': 1}, {'tags': []}):
            response = patch(client, f'/notes/{note.pk}', auth, payload)
            assert response.status_code == 402 and response.json()['detail'] == LOCKED

    def test_a_taken_down_note_is_a_409(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')
        takedown.take_down_note(note, by=make_user(is_staff=True), reason='x')

        response = patch(client, f'/notes/{note.pk}', auth, {'title': 'x', 'base_version': 1})

        assert response.status_code == 409 and response.json()['detail'] == TAKEN_DOWN

    def test_another_users_note_is_a_404(self, client, auth, other_user):
        note = create_note(other_user, title='T')

        assert (
            patch(client, f'/notes/{note.pk}', auth, {'title': 'x', 'base_version': 1}).status_code
            == 404
        )

    def test_an_unknown_key_is_a_422(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')

        for payload in ({'status': 'done'}, {'archived_at': None}, {'version': 3}):
            assert patch(client, f'/notes/{note.pk}', auth, payload).status_code == 422

    def test_the_edit_budget(self, client, auth, funded_user, settings):
        note = create_note(funded_user, title='T')
        limit_rule(settings, 'NOTES_EDIT_USER', limit=1)

        assert patch(client, f'/notes/{note.pk}', auth, {'tags': []}).status_code == 200
        assert patch(client, f'/notes/{note.pk}', auth, {'tags': []}).status_code == 429


class TestChecklist:
    def test_tick_and_untick(self, client, auth, funded_user):
        note = create_note(funded_user, title='T', content='- [ ] a\n- [ ] b\n')

        ticked = post(client, f'/notes/{note.pk}/checklist', auth, {'index': 1, 'checked': True})
        assert ticked.status_code == 200
        assert ticked.json()['content'] == '- [ ] a\n- [x] b\n' and ticked.json()['version'] == 2
        assert 'checked' in ticked.json()['content_html']

        unticked = post(client, f'/notes/{note.pk}/checklist', auth, {'index': 1, 'checked': False})
        assert unticked.json()['content'] == '- [ ] a\n- [ ] b\n'

    def test_a_missing_item_is_a_400_and_spends_no_budget(
        self, client, auth, funded_user, settings
    ):
        note = create_note(funded_user, title='T', content='- [ ] a\n')
        limit_rule(settings, 'NOTES_EDIT_USER', limit=1)

        for _ in range(3):
            response = post(
                client, f'/notes/{note.pk}/checklist', auth, {'index': 5, 'checked': True}
            )
            assert response.status_code == 400
            assert response.json()['detail'] == 'index: That checklist item no longer exists.'
        assert (
            post(
                client, f'/notes/{note.pk}/checklist', auth, {'index': 0, 'checked': True}
            ).status_code
            == 200
        )

    def test_read_only_states(self, client, auth, funded_user):
        locked = make_note(funded_user, title='L', content='- [ ] a\n', locked_at=timezone.now())
        down = create_note(funded_user, title='D', content='- [ ] a\n')
        takedown.take_down_note(down, by=make_user(is_staff=True), reason='x')
        body = {'index': 0, 'checked': True}

        assert post(client, f'/notes/{locked.pk}/checklist', auth, body).status_code == 402
        assert post(client, f'/notes/{down.pk}/checklist', auth, body).status_code == 409

    def test_a_negative_index_is_a_422(self, client, auth, funded_user):
        note = create_note(funded_user, title='T', content='- [ ] a\n')

        assert (
            post(
                client, f'/notes/{note.pk}/checklist', auth, {'index': -1, 'checked': True}
            ).status_code
            == 422
        )


class TestArchiveTrashRestore:
    def test_archive_and_unarchive(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')

        archived = post(client, f'/notes/{note.pk}/archive', auth)
        assert archived.status_code == 200 and archived.json()['archived_at'] is not None
        assert post(client, f'/notes/{note.pk}/archive', auth).status_code == 200  # idempotent
        assert titles(get(client, '/notes', auth, view='archived')) == ['T']

        back = post(client, f'/notes/{note.pk}/unarchive', auth)
        assert back.json()['archived_at'] is None

    def test_archiving_refusals(self, client, auth, funded_user):
        trashed = create_note(funded_user, title='T')
        trash(trashed)
        locked = make_note(funded_user, title='L', locked_at=timezone.now())
        down = create_note(funded_user, title='D')
        takedown.take_down_note(down, by=make_user(is_staff=True), reason='x')

        assert post(client, f'/notes/{trashed.pk}/archive', auth).status_code == 400
        assert post(client, f'/notes/{locked.pk}/archive', auth).status_code == 402
        assert post(client, f'/notes/{down.pk}/unarchive', auth).status_code == 409

    def test_delete_moves_to_the_trash_and_is_idempotent(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')

        first = delete(client, f'/notes/{note.pk}', auth)
        second = delete(client, f'/notes/{note.pk}', auth)

        assert first.status_code == second.status_code == 204
        assert Note.objects.get(pk=note.pk).deleted_at is not None
        assert get(client, '/notes', auth).json()['count'] == 0

    def test_a_locked_and_a_taken_down_note_can_be_trashed(self, client, auth, funded_user):
        locked = make_note(funded_user, title='L', locked_at=timezone.now())
        down = create_note(funded_user, title='D')
        takedown.take_down_note(down, by=make_user(is_staff=True), reason='x')

        assert delete(client, f'/notes/{locked.pk}', auth).status_code == 204
        assert delete(client, f'/notes/{down.pk}', auth).status_code == 204

    def test_trashing_frees_a_free_slot(self, client, auth, funded_user):
        notes = fill_free_slots(funded_user)
        delete(client, f'/notes/{notes[0].pk}', auth)

        assert get(client, '/settings', auth).json()['free_notes_used'] == 4

    def test_restore_a_free_note_while_a_slot_is_open(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')
        trash(note)

        response = post(client, f'/notes/{note.pk}/restore', auth)

        assert response.status_code == 200
        assert response.json()['deleted_at'] is None and response.json()['restore_cost'] is None
        assert balance(funded_user) == 100

    def test_restoring_a_free_note_into_full_slots_costs_and_makes_it_paid(
        self, client, auth, funded_user
    ):
        first = fill_free_slots(funded_user)[0]
        trash(first)
        create_note(funded_user, title='Takes the slot')
        assert get(client, f'/notes/{first.pk}', auth).json()['restore_cost'] == 3

        wrong = post(client, f'/notes/{first.pk}/restore', auth, expected_cost=0)
        assert wrong.status_code == 409 and 'expected_cost=3' in wrong.json()['detail']
        assert balance(funded_user) == 100
        right = post(client, f'/notes/{first.pk}/restore', auth, expected_cost=3)

        assert right.status_code == 200
        body = right.json()
        assert body['is_free'] is False and body['paid_through'] is not None
        assert balance(funded_user) == 97

    def test_restoring_without_funds_is_a_402(self, client, funded_user, config):
        from apps.billing.services import spend_credits

        first = fill_free_slots(funded_user)[0]
        trash(first)
        create_note(funded_user, title='Takes the slot')
        spend_credits(funded_user, 100, source='test', description='drain')

        response = post(client, f'/notes/{first.pk}/restore', bearer(funded_user))

        assert response.status_code == 402
        assert Note.objects.get(pk=first.pk).deleted_at is not None

    def test_restoring_a_note_that_is_not_in_the_trash_changes_nothing(
        self, client, auth, funded_user
    ):
        note = create_note(funded_user, title='T')

        response = post(client, f'/notes/{note.pk}/restore', auth, expected_cost=7)

        assert response.status_code == 200 and response.json()['deleted_at'] is None

    def test_restore_pays_the_renewal_of_a_paid_note_that_fell_due_and_unlocks_it(
        self, client, auth, funded_user
    ):
        note = make_note(
            funded_user,
            title='Due',
            is_free=False,
            paid_through=timezone.now() - timedelta(days=1),
            locked_at=timezone.now(),
            deleted_at=timezone.now(),
        )

        assert get(client, f'/notes/{note.pk}', auth).json()['restore_cost'] == 5
        body = post(client, f'/notes/{note.pk}/restore', auth, expected_cost=5).json()

        assert body['locked'] is False and body['deleted_at'] is None
        assert balance(funded_user) == 95

    def test_delete_forever_needs_the_trash(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')

        assert post(client, f'/notes/{note.pk}/delete-forever', auth).status_code == 404
        trash(note)
        assert post(client, f'/notes/{note.pk}/delete-forever', auth).status_code == 204
        assert not Note.objects.filter(pk=note.pk).exists()
        assert post(client, f'/notes/{note.pk}/delete-forever', auth).status_code == 404

    def test_another_users_note_is_a_404_for_every_action(self, client, auth, other_user):
        note = create_note(other_user, title='T')

        for action in ('archive', 'unarchive', 'restore', 'delete-forever', 'duplicate'):
            assert post(client, f'/notes/{note.pk}/{action}', auth).status_code == 404
        assert delete(client, f'/notes/{note.pk}', auth).status_code == 404


class TestDuplicate:
    def test_a_copy_with_the_content_and_no_tags(self, client, auth, funded_user):
        tag = make_tag(funded_user, 'Work')
        note = create_note(funded_user, title='Plan', content='text', tags=[tag.pk])

        response = post(client, f'/notes/{note.pk}/duplicate', auth)

        assert response.status_code == 201
        body = response.json()
        assert (body['title'], body['content'], body['tags']) == ('Plan (copy)', 'text', [])
        assert body['id'] != str(note.pk) and body['version'] == 1

    def test_it_costs_when_the_slots_are_full_and_expected_cost_guards_it(
        self, client, auth, funded_user
    ):
        notes = fill_free_slots(funded_user)

        wrong = post(client, f'/notes/{notes[0].pk}/duplicate', auth, expected_cost=0)
        right = post(client, f'/notes/{notes[0].pk}/duplicate', auth, expected_cost=3)

        assert wrong.status_code == 409 and right.status_code == 201
        assert right.json()['charged_credits'] == 3 and balance(funded_user) == 97

    def test_a_taken_down_note_cannot_be_copied(self, client, auth, funded_user):
        note = create_note(funded_user, title='T')
        takedown.take_down_note(note, by=make_user(is_staff=True), reason='x')

        response = post(client, f'/notes/{note.pk}/duplicate', auth)

        assert response.status_code == 409
        assert response.json()['detail'] == 'This note was taken down and cannot be copied.'

    def test_a_locked_note_can_be_copied(self, client, auth, funded_user):
        note = make_note(funded_user, title='T', locked_at=timezone.now())

        assert post(client, f'/notes/{note.pk}/duplicate', auth).status_code == 201

    def test_the_create_budget_and_a_refusal_spends_none(self, client, auth, funded_user, settings):
        notes = fill_free_slots(funded_user, 2)
        limit_rule(settings, 'NOTES_CREATE_USER', limit=1)
        row = NotesSettings.load()
        row.max_notes_per_user = 2
        row.save()

        for _ in range(3):  # the cap refuses it: no budget spent
            assert post(client, f'/notes/{notes[0].pk}/duplicate', auth).status_code == 400
        row.max_notes_per_user = 10
        row.save()
        assert post(client, f'/notes/{notes[0].pk}/duplicate', auth).status_code == 201
        assert post(client, f'/notes/{notes[0].pk}/duplicate', auth).status_code == 429


class TestReorder:
    def test_it_writes_the_order(self, client, auth, funded_user):
        notes = [create_note(funded_user, title=name) for name in ('a', 'b', 'c')]
        # Newest first: c, b, a.

        response = post(
            client,
            '/notes/reorder',
            auth,
            {'ids': [str(notes[0].pk), str(notes[1].pk), str(notes[2].pk)]},
        )

        assert response.status_code == 200 and response.json() == {'count': 3}
        assert titles(get(client, '/notes', auth)) == ['a', 'b', 'c']

    def test_ids_that_are_not_yours_or_are_trashed_are_ignored(
        self, client, auth, funded_user, other_user
    ):
        mine = create_note(funded_user, title='mine')
        gone = create_note(funded_user, title='gone')
        trash(gone)
        theirs = create_note(other_user, title='theirs')

        response = post(
            client,
            '/notes/reorder',
            auth,
            {'ids': [str(theirs.pk), str(gone.pk), str(mine.pk), str(uuid.uuid4())]},
        )

        assert response.json() == {'count': 1}

    def test_too_many_ids_is_a_422(self, client, auth):
        ids = [str(uuid.uuid4()) for _ in range(501)]

        assert post(client, '/notes/reorder', auth, {'ids': ids}).status_code == 422
        assert post(client, '/notes/reorder', auth, {'ids': ['x']}).status_code == 422
