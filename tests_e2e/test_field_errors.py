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
    # ... and the ids don't pile up in the description either: the message, then the help text, once.
    expect(destination).to_have_attribute(
        'aria-describedby', 'id_destination-error destination-help'
    )

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
    # The re-rendered form has `autofocus` on Name (the first field): the first invalid control, the
    # password, still takes the focus, since the swap put it on Name, not the person.
    expect(password).to_be_focused()
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

    # A fixed sleep on purpose: it has to outlast the skeleton's 250 ms timer, and an auto-waiting
    # assertion on "no skeleton" would pass at once, before the timer could have shown the rows.
    page.wait_for_timeout(600)
    expect(page.locator('#task-list')).not_to_have_attribute('data-skeleton', '')
    expect(page.locator('#task-list')).not_to_have_attribute('aria-busy', 'true')

    # Editing the field the message is about takes it away (another field's edit leaves it).
    page.locator('#quick-add-form input[name="due_date"]').fill('2030-01-15')
    expect(time_input).to_have_attribute('aria-invalid', 'true')
    time_input.fill('10:30')
    expect(time_input).not_to_have_attribute('aria-invalid', 'true')
    expect(page.locator('[data-field-error-message]')).to_have_count(0)


COMMENT_FORM = '#task-comments form[hx-post$="/comments/"]'
COMMENT_BODY = f'{COMMENT_FORM} textarea[name="body"]'


def _open_task(page: Page, server: ReceiptsServer, title: str) -> None:
    """Create a task and open its page."""
    server.login(page)
    page.goto(f'{server.url}/todo/all/')
    page.locator('#quick-title').fill(title)
    page.click('#quick-add-form button[type="submit"]')
    page.locator('#task-list').get_by_role('link', name=title).first.click()
    expect(page.locator(COMMENT_FORM)).to_be_visible()


def _refuse_comment(page: Page) -> None:
    """Send the task page's comment form empty: refused into `#comment-msg`, a slot above the form
    and outside it, so the form the errors belong to is the one that sent the request."""
    page.click(f'{COMMENT_FORM} button[type="submit"]')


def test_the_errors_go_to_the_form_that_sent_the_request_not_the_latest_one_on_the_page(
    page: Page, receipts_server: ReceiptsServer
):
    """A request from another form while the submit is in flight (the Receipts list polls from inside
    its filter form) must not take the answer's errors away from the form that was submitted."""
    _open_task(page, receipts_server, 'plan the move')

    held = []
    page.route('**/todo/*/comments/', lambda route: held.append(route))
    _refuse_comment(page)
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    assert held, 'the form was not submitted'

    # Meanwhile another form makes a request of its own (as a poll would).
    page.evaluate(
        """() => { document.body.insertAdjacentHTML('beforeend',
            '<form id="other-form"><input id="other-input" name="q"></form>'); }"""
    )
    with page.expect_response('**/todo/filters/counts/**'):
        page.evaluate(
            "() => htmx.ajax('GET', '/todo/filters/counts/', {source: '#other-input', swap: 'none'})"
        )

    held[0].continue_()
    page.unroute('**/todo/*/comments/')

    body = page.locator(COMMENT_BODY)
    expect(page.locator('#comment-msg')).to_contain_text('Write a comment.')
    expect(body).to_have_attribute('aria-invalid', 'true')
    expect(body.locator('xpath=following-sibling::p[1]')).to_have_text('Write a comment.')
    expect(page.locator('#other-form [data-field-error-message]')).to_have_count(0)


def test_a_poll_inside_the_form_does_not_take_its_messages_away(
    page: Page, receipts_server: ReceiptsServer
):
    """Only a new attempt at the whole form (the form or its submit button) clears its messages."""
    _open_task(page, receipts_server, 'book the venue')
    _refuse_comment(page)
    body = page.locator(COMMENT_BODY)
    expect(body).to_have_attribute('aria-invalid', 'true')

    page.evaluate(
        """(selector) => { const form = document.querySelector(selector);
                   form.insertAdjacentHTML('beforeend', '<div id="poller" hx-get="/todo/filters/counts/" ' +
                       'hx-trigger="every 200ms" hx-swap="none" hx-disabled-elt="unset"></div>');
                   htmx.process(form); }""",
        COMMENT_FORM,
    )
    for _ in range(2):
        with page.expect_response('**/todo/filters/counts/**'):
            pass

    expect(body).to_have_attribute('aria-invalid', 'true')
    expect(page.locator(f'{COMMENT_FORM} [data-field-error-message]')).to_have_count(1)

    # Submitting the form again does start clean: one message again, not a second one.
    _refuse_comment(page)
    expect(page.locator('#comment-msg')).to_contain_text('Write a comment.')
    expect(page.locator(f'{COMMENT_FORM} [data-field-error-message]')).to_have_count(1)
    expect(body).to_have_attribute('aria-invalid', 'true')


_INJECT = """([html]) => {
    document.body.insertAdjacentHTML('beforeend', html);
    const form = document.getElementById('probe-form');
    form.dispatchEvent(new CustomEvent('htmx:afterSettle', {bubbles: true, detail: {}}));
}"""


def test_two_errors_for_one_control_both_stay_described_and_both_go_when_it_is_edited(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')
    page.evaluate(
        _INJECT,
        [
            '<form id="probe-form"><label>Name <input id="probe" name="a" data-error-for="b" '
            'aria-describedby="probe-help"></label><span id="probe-help">Help.</span>'
            '<ul hidden data-field-errors><li data-field-error="a">First.</li>'
            '<li data-field-error="b">Second.</li></ul></form>'
        ],
    )
    probe = page.locator('#probe')

    expect(page.locator('#probe-form [data-field-error-message]')).to_have_count(2)
    # Both are in the description, in the order shown, ahead of the help text that was there.
    expect(probe).to_have_attribute('aria-describedby', 'probe-error probe-error-2 probe-help')
    expect(page.locator('#probe-error')).to_contain_text('First.')
    expect(page.locator('#probe-error + #probe-error-2')).to_contain_text('Second.')

    probe.fill('x')
    expect(page.locator('#probe-form [data-field-error-message]')).to_have_count(0)
    expect(probe).not_to_have_attribute('aria-invalid', 'true')
    expect(probe).to_have_attribute('aria-describedby', 'probe-help')


def test_an_autofocus_field_the_swap_rendered_is_not_typing_but_a_field_the_person_is_in_is(
    page: Page, receipts_server: ReceiptsServer
):
    """A form swapped whole renders its `autofocus` field and the browser focuses it; that must not
    keep the focus from the first invalid control. A person typing in another field of the form
    still keeps theirs."""
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')
    fields = (
        '<input id="first" name="first" autofocus><input id="second" name="second">'
        '<input id="third" name="third">'
    )
    errors = '<ul hidden data-field-errors><li data-field-error="second">Fix.</li></ul>'
    settle = """(id) => document.getElementById('probe-form').dispatchEvent(
        new CustomEvent('htmx:afterSettle', {bubbles: true, detail: {}}))"""

    # The person is in `third` when an answer brings an error for `second`: they stay there.
    page.evaluate(
        "([html]) => document.body.insertAdjacentHTML('beforeend', html)",
        [f'<form id="probe-form">{fields}{errors}</form>'],
    )
    page.evaluate("() => document.getElementById('third').focus()")
    page.evaluate(settle)
    expect(page.locator('#second')).to_have_attribute('aria-invalid', 'true')
    expect(page.locator('#third')).to_be_focused()

    # The swap put the focus on the form's `autofocus` field (the person was not on it).
    page.evaluate("() => document.getElementById('probe-form').remove()")
    page.evaluate(
        "([html]) => document.body.insertAdjacentHTML('beforeend', html)",
        [f'<form id="probe-form">{fields}{errors}</form>'],
    )
    page.evaluate("() => document.getElementById('first').focus()")
    page.evaluate(settle)
    expect(page.locator('#second')).to_be_focused()


def test_names_and_ids_with_quotes_and_backslashes_are_found(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')
    page.evaluate(
        _INJECT,
        [
            '<form id="probe-form"><input id="plain" name="we&quot;ird\\name">'
            '<input type="hidden" id="mirror&quot;id" name="mirror">'
            '<input id="shown" type="text" data-utc-target="mirror&quot;id">'
            '<ul hidden data-field-errors><li data-field-error="we&quot;ird\\name">One.</li>'
            '<li data-field-error="mirror">Two.</li></ul></form>'
        ],
    )

    expect(page.locator('#plain')).to_have_attribute('aria-invalid', 'true')
    expect(page.locator('#shown')).to_have_attribute('aria-invalid', 'true')
    expect(page.locator('#probe-form [data-field-error-message]')).to_have_count(2)


def test_a_click_in_the_date_picker_edits_its_field_but_a_click_in_any_relative_box_does_not(
    page: Page, receipts_server: ReceiptsServer
):
    """The picker's button is the control of a hidden date value, so choosing a day (a click, not an
    input event) must clear its message. That is tied to the picker's own `data-field-wrap`, not to
    the very common `.relative` class."""
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')
    page.evaluate(
        _INJECT,
        [
            '<form id="probe-form">'
            '<div class="relative" id="plain-box"><input type="hidden" name="d1">'
            '<button type="button" id="b1">Pick</button><button type="button" id="other1">Other</button></div>'
            '<div data-field-wrap id="wrap-box"><input type="hidden" name="d2">'
            '<button type="button" id="b2">Pick</button><button type="button" id="other2">Other</button></div>'
            '<ul hidden data-field-errors><li data-field-error="d1">One.</li>'
            '<li data-field-error="d2">Two.</li></ul></form>'
        ],
    )
    expect(page.locator('#b1')).to_have_attribute('aria-invalid', 'true')
    expect(page.locator('#b2')).to_have_attribute('aria-invalid', 'true')

    page.click('#other1')  # inside a plain `.relative` box: not the picker
    expect(page.locator('#b1')).to_have_attribute('aria-invalid', 'true')

    page.click('#other2')  # inside the picker's wrapper
    expect(page.locator('#b2')).not_to_have_attribute('aria-invalid', 'true')
    expect(page.locator('#b1')).to_have_attribute('aria-invalid', 'true')
