"""World Time: the grid end to end in a real browser.

Adding a city by one of its other names and a time zone by its abbreviation, picking a time range
with the mouse and with the keyboard, typing an exact start time, the day picker, renaming and
removing a row, and reordering rows with the arrow keys.
"""

import re

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

SETUP = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.world_time.models import City, CityName, Tab\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "user.timezone = 'Europe/Lisbon'\n"
    "user.save(update_fields=['timezone'])\n"
    'Tab.objects.filter(user=user).delete()\n'
    "city, _ = City.objects.update_or_create(geoname_id=1273294, defaults=dict(name='Delhi',"
    " search_name='delhi', country_code='IN', country='India', region='Delhi',"
    " timezone='Asia/Kolkata', population=10927986))\n"
    "CityName.objects.get_or_create(city=city, name='Dilli', search_name='dilli')\n"
)
CLEANUP = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.world_time.models import City, Tab\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "user.timezone = ''\n"
    "user.save(update_fields=['timezone'])\n"
    'Tab.objects.filter(user=user).delete()\n'
    'City.objects.filter(geoname_id=1273294).delete()\n'
)


def _rows(page: Page) -> list[str]:
    return page.eval_on_selector_all(
        '#wt-grid .drag-handle', 'els => els.map(e => e.getAttribute("data-name"))'
    )


def _add(page: Page, typed: str, suggestion: str) -> None:
    box = page.get_by_label('Add a location')
    box.fill(typed)
    option = page.locator('[data-wt-suggestion]', has_text=suggestion).first
    expect(option).to_be_visible()
    with page.expect_response(lambda r: '/locations/' in r.url and r.request.method == 'POST'):
        option.click()
    expect(box).to_have_value('')


def test_the_grid(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(SETUP)
    try:
        receipts_server.login(page)
        # A winter day with no clock change anywhere in the grid, so the times below are fixed.
        page.goto(f'{receipts_server.url}/time/?date=2026-12-10')
        assert _rows(page) == ['Lisbon'], 'a first visit: the account zone, as the home row'
        expect(page.locator('#wt-grid .wt-tile')).to_have_count(24)

        # A city by another of its names; a time zone by its abbreviation.
        _add(page, 'dilli', 'Delhi')
        _add(page, 'pst', 'Los Angeles')
        assert _rows(page) == ['Lisbon', 'Delhi', 'Los Angeles']
        expect(page.locator('#wt-grid .wt-tile')).to_have_count(72)
        expect(page.locator('#wt-search-results')).to_be_empty()

        # Clicking 9 AM on the home row selects that hour, shown in every location.
        tiles = page.locator('#wt-grid [data-wt-tiles]').first.locator('.wt-tile')
        tiles.nth(9).click(position={'x': 3, 'y': 10})
        lines = page.locator('#wt-grid [data-wt-lines] > li')
        expect(lines).to_have_count(3)
        expect(lines.nth(0)).to_contain_text('Thu 12/10/2026, 9:00 AM – 10:00 AM')
        expect(lines.nth(1)).to_contain_text('Thu 12/10/2026, 2:30 PM – 3:30 PM')
        expect(lines.nth(2)).to_contain_text('Thu 12/10/2026, 1:00 AM – 2:00 AM')
        expect(page.get_by_label('Start')).to_have_value('9:00 AM')

        # Dragging selects a range, snapped to half hours.
        first, last = tiles.nth(14).bounding_box(), tiles.nth(16).bounding_box()
        assert first is not None and last is not None
        page.mouse.move(first['x'] + 2, first['y'] + 10)
        page.mouse.down()
        page.mouse.move(last['x'] + last['width'] / 2, last['y'] + 10, steps=6)
        page.mouse.up()
        expect(lines.nth(0)).to_contain_text('2:00 PM – 4:30 PM')
        expect(page.locator('#wt-grid section')).to_contain_text('2 h 30 min')

        # The keyboard: the arrows move the range, Shift makes it longer, Escape clears it.
        grid = page.get_by_role('group', name=re.compile('^Hours of'))
        grid.focus()
        page.keyboard.press('ArrowRight')
        expect(lines.nth(0)).to_contain_text('2:30 PM – 5:00 PM')
        page.keyboard.press('Shift+ArrowLeft')
        expect(lines.nth(0)).to_contain_text('2:30 PM – 4:30 PM')
        expect(page.locator('#wt-grid p.sr-only[aria-live]')).to_contain_text('Lisbon time')

        # An exact start, typed on the home row's clock; the length is kept.
        start = page.get_by_label('Start')
        start.fill('10:10')
        start.press('Enter')
        expect(lines.nth(0)).to_contain_text('10:10 AM – 4:30 PM')
        expect(lines.nth(1)).to_contain_text('3:40 PM – 10:00 PM')

        # Adding a row keeps the selection (the grid is re-rendered).
        _add(page, 'utc', 'UTC')
        expect(lines).to_have_count(4)
        expect(lines.nth(3)).to_contain_text('10:10 AM – 4:30 PM')
        grid.focus()
        page.keyboard.press('Escape')
        expect(page.locator('#wt-grid section')).to_be_hidden()

        # Rename a row, then make another the home: the offsets follow.
        row = page.locator('#wt-grid li[data-sort-id]', has_text='Delhi')
        row.get_by_role('button', name='Actions for Delhi').click()
        row.get_by_role('button', name='Rename', exact=True).click()
        label = page.get_by_label('Your name for Delhi')
        label.fill('India office')
        label.press('Enter')
        expect(page.locator('#wt-grid')).to_contain_text('India office')
        row = page.locator('#wt-grid li[data-sort-id]', has_text='India office')
        row.get_by_role('button', name='Actions for India office').click()
        row.get_by_role('button', name='Make home').click()
        lisbon = page.locator('#wt-grid li[data-sort-id]', has_text='Lisbon')
        expect(lisbon).to_contain_text('-5:30')

        # The arrow keys on a handle reorder the rows; the order is saved.
        expect(page.locator('#wt-grid [data-sortable][data-sortable-ready]')).to_be_visible()
        handle = page.locator('#wt-grid .drag-handle[data-name="UTC"]')
        handle.focus()
        with page.expect_response(lambda r: r.url.endswith('/locations/reorder/')) as saved:
            page.keyboard.press('ArrowUp')
            page.keyboard.press('ArrowUp')
        assert saved.value.status == 204
        expect(handle).to_be_focused()
        assert _rows(page) == ['Lisbon', 'UTC', 'India office', 'Los Angeles']

        # Remove a row (asked first); the day shown is kept.
        row = page.locator('#wt-grid li[data-sort-id]', has_text='Los Angeles')
        row.get_by_role('button', name='Actions for Los Angeles').click()
        row.get_by_role('button', name='Remove').click()
        page.get_by_role('dialog').get_by_role('button', name='Remove').click()
        page.wait_for_url(re.compile(r'/time/[0-9a-f-]+/\?date=2026-12-10$'))
        assert _rows(page) == ['Lisbon', 'UTC', 'India office']

        # The next day, and a second tab.
        page.get_by_role('link', name='Next day').click()
        page.wait_for_url(re.compile(r'date=2026-12-11$'))
        expect(page.locator('#wt-grid .wt-tile--day-start').first).to_contain_text('Fri')
        page.get_by_role('button', name='New tab').click()
        page.get_by_label('Name of the new tab').fill('Family')
        page.get_by_role('button', name='Add tab').click()
        page.wait_for_url(re.compile(r'/time/[0-9a-f-]+/$'))
        expect(page).to_have_title('Family - World Time - ZipTrigo')
        assert _rows(page) == ['Lisbon']
    finally:
        receipts_server.shell(CLEANUP)
