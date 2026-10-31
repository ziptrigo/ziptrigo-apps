"""Field errors (issue #219), end to end in a real browser: a refused form shows each message
under the field that failed, ties it to the field for assistive technology, and takes both away
as soon as the person edits the field.

Run on the shared e2e server (`receipts_server`), which hosts every product.
"""

import re

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

RED_400 = 'rgb(248, 113, 113)'


def test_a_refused_destination_is_explained_under_its_field_until_it_is_edited(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/links/new/')
    destination = page.locator('#id_destination')
    message = page.locator('#id_destination-error')

    # A well-formed address the server refuses (it is not a public host): `type=url` lets it through.
    destination.fill('http://localhost/private')
    page.click('button:has-text("Create link")')

    # The slot says it as before ...
    expect(page.locator('#link-form-msg')).to_contain_text('Destination:')
    # ... and the field has it too, directly under it, with the icon and the error colour.
    expect(message).to_be_visible()
    expect(message).to_contain_text('not allowed')
    expect(message.locator('i.fa-circle-exclamation')).to_have_count(1)
    expect(message).to_have_css('color', 'rgb(220, 38, 38)')
    expect(page.locator('#id_destination + p')).to_have_id('id_destination-error')
    # The red border shows without the field being focused (`site.css`, `aria-invalid`).
    expect(destination).to_have_attribute('aria-invalid', 'true')
    page.locator('h1').click()
    expect(destination).to_have_css('border-top-color', RED_400)
    # The message comes first in `aria-describedby`; the help text that was there is kept.
    expect(destination).to_have_attribute(
        'aria-describedby', 'id_destination-error destination-help'
    )

    # Typing clears the message and both attributes, and gives the help text back as the description.
    destination.press('End')
    destination.type('/')
    expect(message).to_have_count(0)
    expect(destination).not_to_have_attribute('aria-invalid', 'true')
    expect(destination).to_have_attribute('aria-describedby', 'destination-help')
    expect(destination).to_have_css('border-top-color', re.compile(r'^(?!rgb\(248, 113, 113\))'))


def test_the_first_refused_field_takes_the_focus_and_a_new_attempt_starts_clean(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/links/new/')
    destination = page.locator('#id_destination')

    destination.fill('http://localhost/private')
    page.click('button:has-text("Create link")')

    expect(page.locator('#id_destination-error')).to_be_visible()
    expect(destination).to_be_focused()

    # A second refusal replaces the message instead of stacking another one.
    destination.fill('http://127.0.0.1/other')
    page.click('button:has-text("Create link")')
    expect(page.locator('[data-field-error-message]')).to_have_count(1)
    expect(page.locator('#id_destination-error')).to_have_count(1)
    expect(page.locator('#link-form-msg')).to_contain_text('Destination:')

    # A good address goes through, and nothing is left on the page that points at the old attempt.
    destination.fill('https://example.com/ok')
    page.click('button:has-text("Create link")')
    expect(page.locator('body')).to_contain_text('Link created.')


def test_a_label_wrapped_field_keeps_its_name_and_the_message_sits_below_the_label(
    page: Page, receipts_server: ReceiptsServer
):
    """The register form wraps each input in its label: the message goes after the label, so the
    input's accessible name stays its label's instead of absorbing the message, which is its
    description."""
    page.context.clear_cookies()
    page.goto(f'{receipts_server.url}/account/register/')
    page.fill('input[name="name"]', 'Pat')
    page.fill('input[name="email"]', 'field.errors@example.com')
    password = page.locator('input[name="password"]')
    password.fill('abcdefgh')  # long enough for the browser, but with no digit
    page.click('button[type="submit"]')

    message = page.locator('#register-form-password-error')
    expect(message).to_be_visible()
    expect(page.locator('#register-form label:has(input[name="password"]) + p')).to_have_id(
        'register-form-password-error'
    )
    expect(password).to_have_attribute('aria-invalid', 'true')
    expect(password).to_have_accessible_name(re.compile(r'^Password'))
    expect(password).not_to_have_accessible_name(re.compile(r'must contain'))
    expect(password).to_have_accessible_description(re.compile(r'must contain at least one digit'))
    # The other fields have nothing to say.
    expect(page.locator('input[name="email"]')).not_to_have_attribute('aria-invalid', 'true')
    expect(page.locator('[data-field-error-message]')).to_have_count(1)

    password.fill('abcdefg1')
    expect(message).to_have_count(0)
    expect(password).not_to_have_attribute('aria-invalid', 'true')


def test_a_refused_quick_add_marks_the_time_and_leaves_the_task_list_alone(
    page: Page, receipts_server: ReceiptsServer
):
    """The To-do quick add answers a refusal into its own slot (`HX-Retarget`), not the list it
    posts for. The message still lands under the field, and the list's skeleton rows, which a slow
    answer would show, are not left behind by the answer that never swaps into the list."""
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')
    page.locator('#quick-title').fill('call the bank')
    page.locator('#quick-add-form input[name="due_time"]').fill('09:00')
    page.click('#quick-add-form button[type="submit"]')

    time_input = page.locator('#quick-add-form input[name="due_time"]')
    expect(page.locator('#quick-add-msg')).to_contain_text('Time: A time needs a date.')
    expect(time_input).to_have_attribute('aria-invalid', 'true')
    expect(time_input.locator('xpath=following-sibling::p[1]')).to_have_text('A time needs a date.')
    # The first refused field takes the focus (the submit button had it).
    expect(time_input).to_be_focused()

    page.wait_for_timeout(600)  # past the skeleton's 250 ms delay
    expect(page.locator('#task-list')).not_to_have_attribute('data-skeleton', '')
    expect(page.locator('#task-list')).not_to_have_attribute('aria-busy', 'true')

    # Editing the field the message is about takes it away (another field's edit leaves it).
    page.locator('#quick-add-form input[name="due_date"]').fill('2030-01-15')
    expect(time_input).to_have_attribute('aria-invalid', 'true')
    time_input.fill('10:30')
    expect(time_input).not_to_have_attribute('aria-invalid', 'true')
    expect(page.locator('[data-field-error-message]')).to_have_count(0)
