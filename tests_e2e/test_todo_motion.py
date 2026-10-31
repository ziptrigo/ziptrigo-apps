"""The refresh's motion (issue #214), end to end in a real browser: completing a To-do task and
the sliding underline of the filter tabs.

Run on the shared e2e server (`receipts_server`), which hosts every product.
"""

import re

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer


def _add_task(page: Page, title: str) -> None:
    page.locator('#quick-title').fill(title)
    page.click('#quick-add-form button[type="submit"]')
    expect(page.locator('#task-list').get_by_text(title).first).to_be_visible()


def test_completing_a_task_marks_the_row_done_then_it_leaves(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')
    _add_task(page, 'water the plants')
    row = page.locator('#task-list li.task-row', has_text='water the plants').first

    row.locator('[data-complete]').click()

    # The row is `done` the moment the circle is clicked (the tick draws, the title strikes) ...
    expect(row).to_have_class(re.compile(r'\bdone\b'))
    # ... and then it leaves instead of staying: on the All page a completed task moves to Completed.
    expect(page.locator('#task-list').get_by_text('water the plants')).to_have_count(0)
    page.goto(f'{receipts_server.url}/todo/completed/')
    expect(page.locator('#task-list').get_by_text('water the plants').first).to_be_visible()


def test_a_completed_task_is_drawn_complete_without_animating(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')
    _add_task(page, 'file the taxes')
    page.locator('#task-list li.task-row', has_text='file the taxes').first.locator(
        '[data-complete]'
    ).click()
    expect(page.locator('#task-list').get_by_text('file the taxes')).to_have_count(0)

    page.goto(f'{receipts_server.url}/todo/completed/')

    done = page.locator('#task-list li', has_text='file the taxes').first.locator('.is-done')
    expect(done).to_be_visible()
    offset = done.locator('.check-svg path').evaluate('el => getComputedStyle(el).strokeDashoffset')
    assert offset == '0px'


def test_the_tab_underline_slides_to_the_chosen_tab(page: Page, receipts_server: ReceiptsServer):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/receipts/')
    nav = page.locator('[data-tabs]')
    indicator = nav.locator('.tab-indicator')
    expect(indicator).to_have_count(1)
    first_left = indicator.evaluate('el => el.style.left')

    nav.get_by_role('link', name='Failed').click()

    expect(nav.locator('[aria-current="page"]')).to_contain_text('Failed')
    expect(nav.locator('.tab-indicator')).to_have_count(1)
    page.wait_for_function(
        """(before) => { const i = document.querySelector('[data-tabs] .tab-indicator'); return i && i.style.left !== before; }""",
        arg=first_left,
    )
    # It rests under the current tab.
    page.wait_for_function(
        """() => { const nav = document.querySelector('[data-tabs]'); const i = nav.querySelector('.tab-indicator');
                  const tab = nav.querySelector('[aria-current="page"]');
                  return Math.abs(i.getBoundingClientRect().left - tab.getBoundingClientRect().left) < 1; }"""
    )
