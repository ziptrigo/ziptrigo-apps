"""Task references (issue #180): which tasks a text mentions, the stored `TaskReference` rows and
their upkeep, `referenced_by` and the "Referenced by" panel."""

from html import unescape

import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.core.markdown import MAX_MARKDOWN_CHARS

from ..models import Task, TaskReference
from ..services import (
    add_comment,
    complete_task,
    create_task,
    delete_comment,
    edit_comment,
    extract_task_numbers,
    referenced_by,
    resolver_for,
    restore_comment,
    restore_task,
    sync_references,
    toggle_checklist,
    trash_task,
    update_task,
)
from .factories import make_task, make_user

pytestmark = pytest.mark.django_db


def text(response) -> str:
    return unescape(response.content.decode())


def pairs(from_task: Task | None = None) -> set[tuple[int, int, bool]]:
    """`(from number, to number, in a comment)` of every stored reference."""
    rows = TaskReference.objects.select_related('from_task', 'to_task')
    if from_task is not None:
        rows = rows.filter(from_task=from_task)
    return {(row.from_task.number, row.to_task.number, row.comment_id is not None) for row in rows}


class TestExtractTaskNumbers:
    def test_hash_numbers_in_order_once_each(self):
        assert extract_task_numbers('#1003 then #1001, #1003 and #1002.') == [1003, 1001, 1002]

    def test_task_urls_site_relative_and_absolute(self, settings):
        settings.BASE_URL = 'https://app.example.com'
        source = (
            '[a](/todo/1001/) https://app.example.com/todo/1002/ [c](https://app.example.com/todo/1003)'
            ' [d](https://evil.example.com/todo/1004/) [e](/todo/1005/edit/) [f](/todo/1006/?x=1)'
        )

        assert extract_task_numbers(source) == [1001, 1002, 1003]

    def test_mixed_in_order_of_appearance(self):
        assert extract_task_numbers('#1002 [x](/todo/1001/) #1001') == [1002, 1001]

    @pytest.mark.parametrize(
        'source',
        ['#999', '#12345678901', '`#1234`', '```\n#1234\n```', '[#1234](https://x.com)', ''],
    )
    def test_what_is_not_a_reference(self, source):
        assert extract_task_numbers(source) == []

    def test_a_text_over_the_markdown_size_guard_mentions_nothing(self):
        assert extract_task_numbers('#1234 ' + 'x' * MAX_MARKDOWN_CHARS) == []


class TestSyncOnTasks:
    @pytest.fixture
    def tasks(self, user):
        return {n: make_task(user, number=n) for n in (1001, 1002, 1003)}

    def test_a_description_is_synced_on_create(self, funded_user):
        first = create_task(funded_user, title='First')  # 1000
        second = create_task(funded_user, title='Second', description=f'about #{first.number}')

        assert pairs() == {(second.number, first.number, False)}

    def test_a_task_without_a_description_makes_no_rows(self, funded_user):
        create_task(funded_user, title='Plain')

        assert not TaskReference.objects.exists()

    def test_editing_the_description_adds_and_removes_rows(self, user, tasks):
        task = make_task(user, number=2000, description='#1001 #1002')
        sync_references(task)
        assert pairs(task) == {(2000, 1001, False), (2000, 1002, False)}

        update_task(task, changes={'description': '#1002 and [x](/todo/1003/)'})
        assert pairs(task) == {(2000, 1002, False), (2000, 1003, False)}

        update_task(task, changes={'description': ''})
        assert pairs(task) == set()

    def test_other_edits_leave_the_rows_alone(self, user, tasks):
        task = make_task(user, number=2000, description='#1001')
        sync_references(task)
        row = TaskReference.objects.get()

        update_task(task, changes={'title': 'Renamed', 'priority': 2})

        assert TaskReference.objects.get().pk == row.pk

    def test_ticking_a_checklist_box_keeps_the_references(self, user, tasks):
        task = make_task(user, number=2000, description='- [ ] call about #1001')
        sync_references(task)

        toggle_checklist(task, 0, True)

        assert pairs(task) == {(2000, 1001, False)}

    def test_a_self_reference_is_not_stored(self, user, tasks):
        task = make_task(user, number=2000, description='this is #2000, see #1001')

        sync_references(task)

        assert pairs(task) == {(2000, 1001, False)}

    def test_another_users_number_is_ignored(self, user, other_user):
        make_task(other_user, number=1001)
        task = make_task(user, number=2000, description='#1001')

        sync_references(task)

        assert pairs() == set()

    def test_a_number_nobody_has_is_ignored(self, user):
        task = make_task(user, number=2000, description='#8888')

        sync_references(task)

        assert pairs() == set()

    def test_a_trashed_task_is_recorded(self, user, tasks):
        task = make_task(user, number=2000, description='#1001 #1002')
        sync_references(task)
        trash_task(tasks[1001])

        # Trashing a target leaves its row, and a later sync of the text keeps it.
        update_task(task, changes={'description': '#1001 #1002 #1003'})

        assert pairs(task) == {(2000, 1001, False), (2000, 1002, False), (2000, 1003, False)}

    def test_a_task_that_is_trashed_when_mentioned_is_recorded(self, user, tasks):
        trash_task(tasks[1001])
        task = make_task(user, number=2000, description='#1001')

        sync_references(task)

        assert pairs(task) == {(2000, 1001, False)}

    def test_a_completed_task_is_recorded(self, funded_user):
        done = create_task(funded_user, title='Done')
        complete_task(done)

        task = create_task(funded_user, title='Later', description=f'#{done.number}')

        assert pairs(task) == {(task.number, done.number, False)}

    def test_syncing_twice_changes_nothing(self, user, tasks):
        task = make_task(user, number=2000, description='#1001 #1002')

        sync_references(task)
        sync_references(task)

        assert TaskReference.objects.count() == 2

    def test_a_deleted_task_takes_its_rows_with_it(self, user, tasks):
        task = make_task(user, number=2000, description='#1001')
        sync_references(task)

        tasks[1001].delete()

        assert TaskReference.objects.count() == 0
        task.delete()


class TestSyncOnComments:
    @pytest.fixture
    def target(self, user):
        return make_task(user, number=1001)

    @pytest.fixture
    def source(self, user):
        return make_task(user, number=2000, description='')

    def test_add_edit_delete_restore(self, user, source, target):
        other = make_task(user, number=1002)
        comment = add_comment(source, user, 'see #1001')
        assert pairs() == {(2000, 1001, True)}

        edit_comment(comment, 'now #1002 instead')
        assert pairs() == {(2000, 1002, True)}

        delete_comment(comment)
        assert pairs() == set()

        restore_comment(comment)
        assert pairs() == {(2000, 1002, True)}
        assert other.pk

    def test_a_comment_and_the_description_keep_their_own_rows(self, user, source, target):
        update_task(source, changes={'description': '#1001'})
        comment = add_comment(source, user, '#1001 again')

        assert TaskReference.objects.filter(from_task=source).count() == 2
        delete_comment(comment)
        assert pairs() == {(2000, 1001, False)}

    def test_two_comments_each_get_a_row(self, user, source, target):
        add_comment(source, user, '#1001')
        add_comment(source, user, '#1001 too')

        assert TaskReference.objects.filter(to_task=target).count() == 2

    def test_a_comment_cannot_reference_its_own_task(self, user, source, target):
        add_comment(source, user, 'I am #2000, and #1001 is related')

        assert pairs() == {(2000, 1001, True)}

    def test_a_reference_to_another_users_number_is_ignored(self, user, other_user, source):
        make_task(other_user, number=1005)

        add_comment(source, user, '#1005')

        assert pairs() == set()

    def test_a_reference_to_a_trashed_task_is_recorded(self, user, source, target):
        trash_task(target)

        add_comment(source, user, '#1001')

        assert pairs() == {(2000, 1001, True)}

    def test_deleting_the_comment_row_deletes_its_references(self, user, source, target):
        comment = add_comment(source, user, '#1001')

        comment.delete()

        assert TaskReference.objects.count() == 0


class TestConstraints:
    def test_a_description_reference_is_unique(self, user):
        a, b = make_task(user, number=1001), make_task(user, number=1002)
        TaskReference.objects.create(from_task=a, to_task=b)

        with pytest.raises(IntegrityError), transaction.atomic():
            TaskReference.objects.create(from_task=a, to_task=b)

    def test_a_comment_reference_is_unique(self, user):
        a, b = make_task(user, number=1001), make_task(user, number=1002)
        comment = add_comment(a, user, 'x')
        TaskReference.objects.create(from_task=a, to_task=b, comment=comment)

        with pytest.raises(IntegrityError), transaction.atomic():
            TaskReference.objects.create(from_task=a, to_task=b, comment=comment)

    def test_the_description_and_a_comment_may_both_mention(self, user):
        a, b = make_task(user, number=1001), make_task(user, number=1002)
        comment = add_comment(a, user, 'x')

        TaskReference.objects.create(from_task=a, to_task=b)
        TaskReference.objects.create(from_task=a, to_task=b, comment=comment)

        assert TaskReference.objects.count() == 2


class TestReferencedBy:
    @pytest.fixture
    def target(self, user):
        return make_task(user, number=1001, title='Target')

    def test_ordered_by_the_referencing_tasks_number(self, user, target):
        late = make_task(user, number=1300, title='Late', description='#1001')
        early = make_task(user, number=1100, title='Early', description='#1001')
        sync_references(late)
        sync_references(early)

        assert [(t.number, c) for t, c in referenced_by(target)] == [(1100, None), (1300, None)]

    def test_a_mention_in_a_comment_comes_with_the_comment(self, user, target):
        mentioner = make_task(user, number=1100)
        comment = add_comment(mentioner, user, 'see #1001')

        [(found, via)] = referenced_by(target)

        assert (found, via) == (mentioner, comment)

    def test_one_task_mentioning_in_several_places_appears_for_each(self, user, target):
        mentioner = make_task(user, number=1100, description='#1001')
        sync_references(mentioner)
        first = add_comment(mentioner, user, '#1001')
        second = add_comment(mentioner, user, '#1001')

        assert [c for _, c in referenced_by(target)] == [None, first, second]

    def test_completed_tasks_count_trashed_ones_do_not(self, user, funded_user, target):
        done = make_task(user, number=1100, description='#1001')
        gone = make_task(user, number=1200, description='#1001')
        sync_references(done)
        sync_references(gone)
        complete_task(done)
        trash_task(gone)

        assert [t.number for t, _ in referenced_by(target)] == [1100]

    def test_a_mention_made_while_the_target_is_trashed_shows_once_it_is_restored(
        self, user, target
    ):
        mentioner = make_task(user, number=1100)
        trash_task(target)
        add_comment(mentioner, user, '#1001')
        update_task(mentioner, changes={'description': 'also #1001'})

        assert referenced_by(target) == []  # a task in the Trash lists nothing

        restore_task(target)

        assert [(t.number, c is not None) for t, c in referenced_by(target)] == [
            (1100, False),
            (1100, True),
        ]

    def test_a_mentioner_trashed_and_restored_is_listed_again(self, user, target):
        mentioner = make_task(user, number=1100, description='#1001')
        sync_references(mentioner)
        trash_task(mentioner)
        assert referenced_by(target) == []

        restore_task(mentioner)

        assert [t.number for t, _ in referenced_by(target)] == [1100]

    def test_a_deleted_comment_is_not_listed(self, user, target):
        mentioner = make_task(user, number=1100)
        comment = add_comment(mentioner, user, '#1001')
        delete_comment(comment)

        assert referenced_by(target) == []

    def test_nothing_when_nobody_mentions_it(self, user, target):
        assert referenced_by(target) == []


class TestResolver:
    def test_each_number_is_looked_up_once_per_resolver(self, user, django_assert_num_queries):
        make_task(user, number=1001)
        resolve = resolver_for(user)

        with django_assert_num_queries(1):
            assert resolve([1001, 8888]) == {1001: '/todo/1001/'}
        with django_assert_num_queries(0):
            assert resolve([8888, 1001]) == {1001: '/todo/1001/'}
        with django_assert_num_queries(1):
            assert resolve([1001, 1002]) == {1001: '/todo/1001/'}

    def test_only_the_users_non_trashed_tasks_link(self, user, other_user):
        make_task(other_user, number=1001)
        gone = make_task(user, number=1002)
        make_task(user, number=1003)
        trash_task(gone)

        assert resolver_for(user)([1001, 1002, 1003]) == {1003: '/todo/1003/'}


class TestSharedResolver:
    def test_a_page_looks_each_number_up_once_for_the_description_and_the_comments(
        self, web, funded_user
    ):
        target = create_task(funded_user, title='Target')
        page = create_task(funded_user, title='Page', description=f'see #{target.number}')
        add_comment(page, funded_user, f'also #{target.number}')
        add_comment(page, funded_user, f'and #{target.number} again')

        with CaptureQueriesContext(connection) as queries:
            response = web.get(reverse('todo:task', args=[page.number]))

        lookups = [q for q in queries if '"number" IN' in q['sql']]
        assert len(lookups) == 1
        body = text(response)
        assert body.count(f'<a href="/todo/{target.number}/" class="task-ref">') == 3


class TestPanel:
    @pytest.fixture
    def pair(self, funded_user):
        target = create_task(funded_user, title='Target task')
        mentioner = create_task(funded_user, title='Mentions it')
        return target, mentioner

    def test_the_panel_is_hidden_without_references(self, web, pair):
        target, _ = pair

        body = text(web.get(reverse('todo:task', args=[target.number])))

        assert 'Referenced by' not in body

    def test_the_panel_lists_a_description_mention(self, web, pair, funded_user):
        target, mentioner = pair
        update_task(mentioner, changes={'description': f'#{target.number}'})

        body = text(web.get(reverse('todo:task', args=[target.number])))

        assert 'Referenced by' in body
        assert f'href="/todo/{mentioner.number}/"' in body
        assert f'#{mentioner.number} Mentions it' in body
        assert 'in a comment' not in body

    def test_the_panel_links_a_comment_mention_with_an_anchor(self, web, pair, funded_user):
        target, mentioner = pair
        comment = add_comment(mentioner, funded_user, f'ref [here](/todo/{target.number}/)')

        body = text(web.get(reverse('todo:task', args=[target.number])))

        assert f'href="/todo/{mentioner.number}/#comment-{comment.pk}"' in body
        assert 'in a comment' in body

    def test_the_panel_follows_a_trashed_mentioner(self, web, pair, funded_user):
        target, mentioner = pair
        add_comment(mentioner, funded_user, f'#{target.number}')
        trash_task(mentioner)

        assert 'Referenced by' not in text(web.get(reverse('todo:task', args=[target.number])))

    def test_a_mention_made_while_the_target_was_trashed_is_listed_after_the_restore(
        self, web, pair, funded_user
    ):
        target, mentioner = pair
        trash_task(target)
        update_task(mentioner, changes={'description': f'#{target.number}'})
        add_comment(mentioner, funded_user, f'and #{target.number}')
        page = reverse('todo:task', args=[target.number])
        assert 'Referenced by' not in text(web.get(page))  # in the Trash: nothing listed

        restore_task(target)

        body = text(web.get(page))
        assert 'Referenced by' in body and f'#{mentioner.number} Mentions it' in body
        assert 'in a comment' in body

    def test_the_description_links_to_the_right_task_and_leaves_unknown_numbers(
        self, web, pair, funded_user
    ):
        target, mentioner = pair
        update_task(mentioner, changes={'description': f'see #{target.number} and #999 and #8888'})

        body = text(web.get(reverse('todo:task', args=[mentioner.number])))

        assert f'<a href="/todo/{target.number}/" class="task-ref">#{target.number}</a>' in body
        assert 'href="/todo/999/"' not in body and 'href="/todo/8888/"' not in body
        assert '#999' in body

    def test_another_users_task_shows_nothing_of_it(self, web, pair, funded_user):
        target, _ = pair
        stranger = make_user()
        theirs = make_task(stranger, number=target.number + 500, description=f'#{target.number}')
        sync_references(theirs)

        assert 'Referenced by' not in text(web.get(reverse('todo:task', args=[target.number])))
