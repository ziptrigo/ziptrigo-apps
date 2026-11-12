"""Notes (issue #260), end to end in a real browser: the editor with its live preview, coloured code
and a table, ticking a box on the note page, reordering the list with the keyboard, archiving, the
Trash and restoring, Share from the row menu, and (PR 5) searching from the sidebar, narrowing the results, saving a filter,
its count in the sidebar and renaming and deleting it.

Run on the shared e2e server (`receipts_server`). Every note this makes has the title prefix
`Zn `; each test removes what it made first and last.
"""

import re

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

CODE = 'def greet(name):\n    return "hi " + name\n'
CONTENT = (
    '```python\n' + CODE + '```\n\n'
    '| name | qty |\n|:-----|----:|\n| eggs | 12 |\n\n'
    '- [ ] first\n- [ ] second\n'
)
CLEANUP = (
    'import os\n'
    'from apps.notes.models import Note, SavedFilter, Tag\n'
    "SavedFilter.objects.filter(user__email=os.environ['E2E_EMAIL'], name__startswith='Zn ').delete()\n"
    "Note.objects.filter(user__email=os.environ['E2E_EMAIL'], title__startswith='Zn ').delete()\n"
    "Tag.objects.filter(user__email=os.environ['E2E_EMAIL'], name__startswith='Zn ').delete()\n"
)
SEED_THREE = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.notes.services import create_note\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "for title in ('Zn A', 'Zn B', 'Zn C'):\n"
    '    create_note(user, title=title)\n'
)


def _titles(page: Page) -> list[str]:
    names = page.eval_on_selector_all(
        '.drag-handle', 'els => els.map(e => e.getAttribute("data-name"))'
    )
    return [name for name in names if name and name.startswith('Zn ')]


def _row_titles(page: Page) -> list[str]:
    names = page.eval_on_selector_all('li.note-row a', 'els => els.map(e => e.textContent.trim())')
    return [name for name in names if name.startswith('Zn ')]


def test_write_preview_save_and_tick_a_box(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(CLEANUP)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/notes/new/')
        page.fill('#note-title', 'Zn Python notes')
        page.fill('#note-content', CONTENT)

        # The live preview follows the textarea after a pause: coloured code, a table, checklist boxes.
        preview = page.locator('#note-preview')
        expect(preview.locator('pre.hl')).to_be_visible()
        expect(preview.locator('table')).to_be_visible()
        expect(preview.locator('td', has_text='eggs')).to_be_visible()
        assert preview.locator('pre.hl span[class^="hl-"]').count() > 0
        page.fill('#note-content', CONTENT + '\n# Added heading\n')
        expect(preview.locator('h1')).to_have_text('Added heading')

        page.click('button[type="submit"]:has-text("Save")')
        page.wait_for_url(re.compile(r'/notes/[0-9a-f-]{36}/$'))
        expect(page.get_by_text('Note created.')).to_be_visible()

        body = page.locator('#note-body')
        # The keyword colour is applied from the generated stylesheet, not the block's own colour.
        keyword = body.locator('pre.hl .hl-k').first
        expect(keyword).to_be_visible()
        colours = page.evaluate(
            """() => {
                const pre = document.querySelector('#note-body pre.hl');
                const key = pre.querySelector('.hl-k');
                return [getComputedStyle(pre).color, getComputedStyle(key).color];
            }"""
        )
        assert colours[0] != colours[1]
        expect(body.locator('div.md-table table')).to_be_visible()
        # The code block has a copy button.
        expect(body.locator('pre.hl .note-copy-code')).to_be_attached()

        # Ticking a box on the rendered note saves it.
        boxes = body.locator('input.task-list-item-checkbox')
        expect(boxes.first).to_be_enabled()
        with page.expect_response(lambda r: r.url.endswith('/checklist/')) as saved:
            boxes.nth(1).check()
        assert saved.value.status == 200
        page.reload()
        expect(page.locator('#note-body input.task-list-item-checkbox').nth(1)).to_be_checked()
        expect(page.locator('#note-body input.task-list-item-checkbox').first).not_to_be_checked()
    finally:
        receipts_server.shell(CLEANUP)


def test_reorder_archive_trash_and_restore(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(CLEANUP)
    receipts_server.shell(SEED_THREE)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/notes/')
        # New notes go first: C, B, A.
        assert _titles(page) == ['Zn C', 'Zn B', 'Zn A']

        # Drag, by keyboard: the last note up to the top. Two quick presses make one save.
        total = page.locator('.drag-handle').count()
        handle = page.locator('.drag-handle[data-name="Zn A"]')
        handle.focus()
        live = page.locator('[data-sortable-live]')
        with page.expect_response(lambda r: r.url.endswith('/notes/reorder/')) as saved:
            page.keyboard.press('ArrowUp')
            page.keyboard.press('ArrowUp')
        assert saved.value.status == 204
        expect(handle).to_be_focused()
        expect(live).to_contain_text('Zn A moved to position')
        assert total >= 3
        assert _titles(page) == ['Zn A', 'Zn C', 'Zn B']
        page.reload()
        assert _titles(page) == ['Zn A', 'Zn C', 'Zn B']

        # Archive one from its page: it leaves the list and shows under Archived.
        page.click('li.note-row a:has-text("Zn C")')
        page.wait_for_url(re.compile(r'/notes/[0-9a-f-]{36}/$'))
        page.click('button:has-text("Archive")')
        expect(page.get_by_text('Note archived.')).to_be_visible()
        page.goto(f'{receipts_server.url}/notes/')
        assert 'Zn C' not in _row_titles(page)
        page.goto(f'{receipts_server.url}/notes/archived/')
        assert 'Zn C' in _row_titles(page)

        # Move one to the Trash, then restore it from there.
        page.goto(f'{receipts_server.url}/notes/')
        page.click('li.note-row a:has-text("Zn B")')
        page.click('button:has-text("Move to trash")')
        expect(page.get_by_text('Note moved to the Trash.')).to_be_visible()
        expect(page.get_by_text('This note is in the Trash.').first).to_be_visible()
        page.goto(f'{receipts_server.url}/notes/trash/')
        assert 'Zn B' in _row_titles(page)
        page.click('li.note-row:has-text("Zn B") button:has-text("Restore")')
        expect(page.get_by_text('Note restored.')).to_be_visible()
        page.goto(f'{receipts_server.url}/notes/')
        assert 'Zn B' in _row_titles(page)
    finally:
        receipts_server.shell(CLEANUP)


def test_an_edit_that_lost_a_race_shows_the_conflict_panel(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.shell(CLEANUP)
    receipts_server.shell(SEED_THREE)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/notes/')
        page.click('li.note-row a:has-text("Zn A")')
        page.click('a:has-text("Edit")')
        page.fill('#note-content', 'my text')
        # Someone else saves first.
        receipts_server.shell(
            'import os\n'
            'from apps.notes.models import Note\n'
            'from apps.notes.services import Editor, save_note\n'
            "note = Note.objects.get(user__email=os.environ['E2E_EMAIL'], title='Zn A')\n"
            "save_note(note, editor=Editor.for_user(note, None), content='their text', base_version=1)\n"
        )
        page.click('button[type="submit"]:has-text("Save")')
        panel = page.locator('#editor-conflict')
        expect(panel).to_contain_text(
            'This note was changed by someone else while you were editing.'
        )
        expect(panel).to_contain_text('their text')
        expect(page.locator('#note-content')).to_have_value('my text')
        page.click('button:has-text("Save mine anyway")')
        page.wait_for_url(re.compile(r'/notes/[0-9a-f-]{36}/$'))
        expect(page.locator('#note-body')).to_contain_text('my text')
    finally:
        receipts_server.shell(CLEANUP)


SEED_SEARCH = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.notes.services import create_note\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "create_note(user, title='Zn Deploy plan', content='ship the release on friday')\n"
    "create_note(user, title='Zn Release notes', content='the deploy steps are in the wiki')\n"
    "create_note(user, title='Zn Groceries', content='eggs and milk')\n"
)


def test_search_narrow_save_a_filter_and_manage_it(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(CLEANUP)
    receipts_server.shell(SEED_SEARCH)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/notes/')

        # The sidebar's box goes to the search page: a title match and a content match, the text
        # highlighted, the unrelated note left out.
        page.fill('#notes-search-q', 'deploy')
        page.press('#notes-search-q', 'Enter')
        page.wait_for_url(re.compile(r'/notes/search/\?q=deploy$'))
        results = page.locator('#search-results')
        expect(results.locator('li.note-row', has_text='Zn Deploy plan')).to_be_visible()
        expect(results.locator('li.note-row', has_text='Zn Release notes')).to_be_visible()
        expect(results.locator('li.note-row', has_text='Zn Groceries')).to_have_count(0)
        expect(results.locator('mark', has_text='deploy').first).to_be_visible()
        expect(results.locator('li.note-row', has_text='Zn Release notes')).to_contain_text(
            'Content:'
        )

        # Changing a control re-fetches the list and the address follows it.
        page.select_option('#search-sort', 'title')
        page.wait_for_url(re.compile(r'q=deploy&sort=title'))
        rows = results.locator('li.note-row a.truncate')
        expect(rows.first).to_have_text('Zn Deploy plan')

        # The direction: title runs ascending by default; reversing it writes `dir`, and choosing a
        # sort puts the direction back to that sort's own default (updated: newest first).
        expect(page.locator('#search-dir')).to_have_value('asc')
        page.select_option('#search-dir', 'desc')
        page.wait_for_url(re.compile(r'q=deploy&sort=title&dir=desc'))
        expect(rows.first).to_have_text('Zn Release notes')
        page.select_option('#search-sort', 'updated')
        expect(page.locator('#search-dir')).to_have_value('desc')
        page.wait_for_url(re.compile(r'q=deploy$'))
        page.select_option('#search-sort', 'title')
        expect(page.locator('#search-dir')).to_have_value('asc')
        page.wait_for_url(re.compile(r'q=deploy&sort=title$'))

        # A refused pattern is a line above the form, not a crash.
        page.fill('#search-q', '/(/')
        page.press('#search-q', 'Enter')
        expect(page.locator('#search-msg')).to_contain_text('Invalid regular expression.')
        page.fill('#search-q', 'deploy*')
        page.press('#search-q', 'Enter')
        expect(results.locator('li.note-row', has_text='Zn Deploy plan')).to_be_visible()

        # Save it as a filter: it lands on the filter's page and is listed in the sidebar with its count.
        page.click('button:has-text("Save as filter")')
        page.fill('#filter-name', 'Zn Deploy')
        page.click('form[action$="/notes/filters/"] button[type="submit"]')
        page.wait_for_url(re.compile(r'/notes/filters/[0-9a-f-]{36}/$'))
        expect(page.get_by_text('Saved the filter Zn Deploy.')).to_be_visible()
        sidebar = page.locator('#sidebar-filters')
        expect(sidebar.get_by_role('link', name=re.compile('Zn Deploy'))).to_be_visible()
        expect(sidebar.locator('[id^="side-count-"]')).to_contain_text('2')

        # The filters page: the count, rename in place, delete through the confirmation.
        page.click('#sidebar-filters >> text=Filters')
        page.wait_for_url(re.compile(r'/notes/filters/$'))
        expect(page.locator('[id^="page-count-"]')).to_contain_text('2 notes')
        page.click('button[aria-label="Rename Zn Deploy"]')
        page.fill('input[name="name"]', 'Zn Shipping')
        page.press('input[name="name"]', 'Enter')
        expect(page.locator('#filters').get_by_role('link', name='Zn Shipping')).to_be_visible()
        expect(sidebar.get_by_role('link', name=re.compile('Zn Shipping'))).to_be_visible()
        page.click('button[aria-label="Delete Zn Shipping"]')
        page.click('[role="dialog"] button[type="submit"]')
        expect(page.get_by_text('Deleted the filter Zn Shipping.')).to_be_visible()
        expect(page.locator('#filters')).to_contain_text('No saved filters yet')
    finally:
        receipts_server.shell(CLEANUP)


SEED_FILTERS = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.notes.services import FilterParams, save_filter\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "for name in ('Zn One', 'Zn Two', 'Zn Three'):\n"
    '    save_filter(user, name, FilterParams(q=name))\n'
)


def test_reordering_filters_reorders_the_sidebar_without_asking_again(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.shell(CLEANUP)
    receipts_server.shell(SEED_FILTERS)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/notes/filters/')
        sidebar_names = page.locator('#sidebar-filters li[data-filter-id] span.truncate')
        expect(sidebar_names).to_have_text(['Zn One', 'Zn Two', 'Zn Three'])
        expect(page.locator('[id^="page-count-"]').first).to_contain_text('note')
        asked: list[str] = []
        page.on('request', lambda request: asked.append(request.url))

        # Move "Zn Three" to the top with the arrow keys (the drag handle's keyboard path).
        page.focus('.drag-handle[data-name="Zn Three"]')
        with page.expect_response(re.compile(r'/notes/filters/reorder/$')) as saved:
            page.keyboard.press('ArrowUp')
            page.keyboard.press('ArrowUp')
        assert saved.value.status == 204

        expect(sidebar_names).to_have_text(['Zn Three', 'Zn One', 'Zn Two'])
        # Only the order was posted: no counts, no sidebar, nothing that spends the search budget.
        page.wait_for_timeout(500)
        assert [url for url in asked if '/notes/filters/' in url] == [
            url for url in asked if url.endswith('/notes/filters/reorder/')
        ]
        page.reload()
        expect(sidebar_names).to_have_text(['Zn Three', 'Zn One', 'Zn Two'])
    finally:
        receipts_server.shell(CLEANUP)


def test_share_from_the_row_menu_opens_the_panel(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(CLEANUP)
    receipts_server.shell(SEED_THREE)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/notes/')
        page.get_by_role('button', name='Actions for Zn B').click()
        items = page.locator('li.note-row:has-text("Zn B") [role="menuitem"]')
        expect(items).to_have_text(['Edit', 'Share', 'Duplicate', 'Archive', 'Move to trash'])
        page.get_by_role('menuitem', name='Share').click()
        page.wait_for_url(re.compile(r'/notes/[0-9a-f-]{36}/\?share=1#share-panel$'))
        expect(page.get_by_text('Share this note')).to_be_visible()
        expect(page.get_by_role('button', name='Create link')).to_be_visible()
    finally:
        receipts_server.shell(CLEANUP)
