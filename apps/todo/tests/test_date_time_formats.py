"""To-do and the date and time format (issue #245): the pages in both formats, the messages that are
always public, the fields that replace the native inputs, and the AI's date-order hint."""

import json
import re
from datetime import date, time

import pytest
from django.urls import reverse

from apps.accounts.services.display_formats import set_display_formats
from apps.core.datetime_format import PUBLIC, DisplayFormats, override

from ..models import Task
from ..services import create_task, get_account, reminders
from ..services.parsing import DATE_ORDER_RULES, ParsedTitle, ParseRequest, build_messages
from .api_support import auth  # noqa: F401
from .api_support import post as api_post

MDY = DisplayFormats('mdy', '12h')
DMY = DisplayFormats('dmy', '24h')
BOTH = pytest.mark.parametrize(
    ('date_format', 'time_format'), [('mdy', '12h'), ('dmy', '24h')], ids=['mdy-12h', 'dmy-24h']
)
TODAY = date(2026, 10, 28)  # `wednesday` freezes now at 12:00 UTC


def page(client, name: str, *args) -> str:
    response = client.get(reverse(name, args=args))
    assert response.status_code == 200
    return re.sub(r'\s+', ' ', response.content.decode())


def timed(user, **fields) -> Task:
    return create_task(
        user, title='Pay rent', due_date=date(2026, 11, 5), due_time=time(21, 5), **fields
    )


# -- the pages -------------------------------------------------------------------------------------


@BOTH
def test_the_task_page_shows_the_due_date_and_time_in_the_users_format(
    web, funded_user, wednesday, date_format, time_format
):
    set_display_formats(funded_user, date_format=date_format, time_format=time_format)
    task = timed(funded_user)

    body = page(web, 'todo:task', task.number)

    if date_format == 'mdy':
        assert 'value="11/05/2026"' in body and 'value="9:05 PM"' in body
    else:
        assert 'value="05/11/2026"' in body and 'value="21:05"' in body
    # The hidden inputs the form submits are ISO whatever the format.
    assert re.search(r'name="due_date" value="2026-11-05"', body)
    assert re.search(r'name="due_time" value="21:05"', body)


@BOTH
def test_a_row_badge_follows_the_format(web, funded_user, wednesday, date_format, time_format):
    set_display_formats(funded_user, date_format=date_format, time_format=time_format)
    create_task(funded_user, title='Today one', due_date=TODAY, due_time=time(21, 5))
    create_task(funded_user, title='Next year', due_date=date(2027, 11, 5), due_time=time(21, 5))

    body = page(web, 'todo:all')

    if date_format == 'mdy':
        assert 'Today 9:05 PM' in body and '11/05/2027, 9:05 PM' in body
    else:
        assert 'Today 21:05' in body and '05/11/2027, 21:05' in body


@BOTH
def test_the_today_and_upcoming_headings_follow_the_order(
    web, funded_user, wednesday, date_format, time_format
):
    set_display_formats(funded_user, date_format=date_format, time_format=time_format)

    today = page(web, 'todo:today')
    upcoming = page(web, 'todo:upcoming')

    if date_format == 'mdy':
        assert 'Wednesday, 10/28' in today and 'Tomorrow, 10/29' in upcoming
        assert 'Friday, 10/30' in upcoming
    else:
        assert 'Wednesday, 28/10' in today and 'Tomorrow, 29/10' in upcoming
        assert 'Friday, 30/10' in upcoming


@BOTH
def test_reminders_and_their_history_follow_the_format(
    web, funded_user, wednesday, date_format, time_format
):
    set_display_formats(funded_user, date_format=date_format, time_format=time_format)
    task = timed(funded_user)
    [reminder] = task.reminders.all()
    from ..models import ReminderDelivery

    ReminderDelivery.objects.create(
        reminder=reminder,
        task=task,
        user=funded_user,
        task_number=task.number,
        task_title=task.title,
        channel='in_app',
        sent_at=reminder.fire_at,
        fire_at=reminder.fire_at,
    )

    body = page(web, 'todo:task', task.number)
    history = page(web, 'todo:reminder-history')

    if date_format == 'mdy':
        assert 'Fires Thu 11/05/2026, 9:00 PM' in body
        assert '11/05/2026, 9:00 PM' in history
    else:
        assert 'Fires Thu 05/11/2026, 21:00' in body
        assert '05/11/2026, 21:00' in history


@BOTH
def test_the_settings_default_time_is_a_time_field(web, funded_user, date_format, time_format):
    set_display_formats(funded_user, date_format=date_format, time_format=time_format)

    body = page(web, 'todo:settings')

    assert 'name="default_due_time" value="09:00"' in body
    shown = '9:00 AM' if time_format == '12h' else '09:00'
    assert 'id="default-due-time"' in body and f'value="{shown}"' in body
    assert 'type="time"' not in body


def test_no_native_date_or_time_input_is_left_on_a_todo_page(web, funded_user, wednesday):
    task = timed(funded_user)

    for body in (
        page(web, 'todo:all'),
        page(web, 'todo:task', task.number),
        page(web, 'todo:search'),
    ):
        assert 'type="date"' not in body
        assert 'type="time"' not in body
        assert 'datetime-local' not in body


def test_the_search_range_fields_are_disabled_until_custom(web, wednesday):
    body = page(web, 'todo:search')

    assert body.count('''x-modelable="value"''') == 2
    assert body.count(''':disabled="range !== 'custom'"''') >= 4


def test_the_repeat_until_date_is_a_field_bound_to_the_control(web, funded_user, wednesday):
    task = timed(funded_user)

    body = page(web, 'todo:task', task.number)

    assert 'x-model="until"' in body
    assert 'type="date"' not in body


def test_completed_and_trashed_dates_follow_the_format(web, funded_user, wednesday):
    set_display_formats(funded_user, date_format='dmy', time_format='24h')
    task = timed(funded_user)
    from ..services import complete_task

    complete_task(task)

    body = page(web, 'todo:task', task.number)

    assert 'Completed 28/10/2026, 12:00' in body


# -- messages are always public ------------------------------------------------------------------------


@pytest.mark.parametrize('formats', [MDY, DMY])
def test_due_label_is_public_whatever_the_owners_format(funded_user, wednesday, formats):
    set_display_formats(funded_user, date_format=formats.date_order, time_format=formats.clock)
    task = timed(funded_user)
    dated = create_task(funded_user, title='Date only', due_date=date(2026, 11, 5))

    with override(formats):
        assert reminders.due_label(task) == 'Due Nov 5, 2026, 9:05 PM'
        assert reminders.due_label(dated) == 'Due Nov 5, 2026'
    assert reminders.due_label(create_task(funded_user, title='t', due_date=TODAY)) == 'Due today'


@pytest.mark.parametrize('formats', [MDY, DMY])
def test_snoozed_label_is_public_whatever_the_owners_format(funded_user, wednesday, formats):
    from datetime import UTC, datetime

    now = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    with override(formats):
        assert reminders.snoozed_label(
            funded_user, datetime(2026, 10, 28, 21, 5, tzinfo=UTC), now
        ) == ('9:05 PM')
        assert reminders.snoozed_label(
            funded_user, datetime(2026, 10, 29, 9, 0, tzinfo=UTC), now
        ) == ('Thu Oct 29, 9:00 AM')


def test_the_reminder_email_is_public_for_a_day_first_24_hour_owner(
    funded_user, wednesday, monkeypatch, django_capture_on_commit_callbacks
):
    set_display_formats(funded_user, date_format='dmy', time_format='24h')
    mails = []
    monkeypatch.setattr(reminders, 'send_email', lambda **kw: mails.append(kw) or (1, 0))
    task = create_task(funded_user, title='Pay rent', due_date=TODAY, due_time=time(14, 30))
    [reminder] = task.reminders.all()
    reminder.channel = 'email'
    reminder.save()

    with django_capture_on_commit_callbacks(execute=True):
        from datetime import UTC, datetime

        reminders.deliver_due(datetime(2026, 10, 28, 14, 26, tzinfo=UTC))

    [mail] = mails
    assert 'Due Oct 28, 2026, 2:30 PM (UTC)' in mail['text_body']
    assert 'Due Oct 28, 2026, 2:30 PM (UTC)' in mail['html_body']
    assert '28/10' not in mail['text_body']


def test_no_template_message_uses_the_active_formats(funded_user, wednesday):
    """A job runs with the public wording active; a request's formats never reach a message."""
    task = timed(funded_user)
    with override(DMY):
        assert reminders.due_label(task) == 'Due Nov 5, 2026, 9:05 PM'
    with override(PUBLIC):
        assert reminders.due_label(task) == 'Due Nov 5, 2026, 9:05 PM'


# -- the AI's date-order hint --------------------------------------------------------------------------


def request_for(formats: DisplayFormats | None = None) -> ParseRequest:
    kwargs = {} if formats is None else {'formats': formats}
    return ParseRequest(
        title='pay rent 5/11',
        today=TODAY,
        weekday='Wednesday',
        timezone='UTC',
        default_time=time(9, 0),
        now_local=time(12, 0),
        **kwargs,
    )


@pytest.mark.parametrize(
    ('formats', 'order', 'fragment'),
    [
        (MDY, 'mdy', '5/11 is May 11'),
        (DMY, 'dmy', '5/11 is 5 November'),
        (PUBLIC, 'mdy', '5/11 is May 11'),
        (None, 'mdy', '5/11 is May 11'),
    ],
)
def test_the_prompt_says_how_the_user_writes_numeric_dates(formats, order, fragment):
    request = request_for(formats)

    system = build_messages(request)[0]['content']

    assert request.date_order == order
    assert DATE_ORDER_RULES[order] in system
    assert fragment in system
    other = 'dmy' if order == 'mdy' else 'mdy'
    assert DATE_ORDER_RULES[other] not in system


@pytest.mark.parametrize(
    ('date_format', 'time_format', 'label'),
    [('mdy', '12h', 'Sun 11/01, 9:00 AM'), ('dmy', '24h', 'Sun 01/11, 09:00')],
)
def test_the_web_endpoint_reads_the_chip_in_the_users_format_and_tells_the_model_the_order(
    web, funded_user, fake_title_parser, wednesday, date_format, time_format, label
):
    set_display_formats(funded_user, date_format=date_format, time_format=time_format)
    fake_title_parser.replies['pay rent 1/11'] = ParsedTitle(
        has_date=True,
        clean_title='pay rent',
        due_date=date(2026, 11, 1),
        due_time=time(9, 0),
        matched_text='1/11',
        confidence=0.9,
    )

    response = web.post(
        reverse('todo:parse'),
        data=json.dumps({'title': 'pay rent 1/11'}),
        content_type='application/json',
    )

    assert response.json()['label'] == label
    assert fake_title_parser.requests[0].date_order == date_format


def test_the_api_endpoint_uses_the_users_formats_too(
    client, auth, funded_user, fake_title_parser, wednesday
):
    set_display_formats(funded_user, date_format='dmy', time_format='24h')
    fake_title_parser.replies['pay rent 1/11'] = ParsedTitle(
        has_date=True,
        clean_title='pay rent',
        due_date=date(2026, 11, 1),
        due_time=time(18, 30),
        matched_text='1/11',
        confidence=0.9,
    )

    body = api_post(client, '/parse', auth, {'title': 'pay rent 1/11'}).json()

    assert body['label'] == 'Sun 01/11, 18:30'
    assert body['due_date'] == '2026-11-01' and body['due_time'] == '18:30'
    assert fake_title_parser.requests[0].date_order == 'dmy'


def test_creating_through_the_api_tells_the_model_the_order(
    client, auth, funded_user, fake_title_parser, wednesday
):
    set_display_formats(funded_user, date_format='dmy')

    api_post(client, '/tasks', auth, {'title': 'pay rent 1/11', 'parse_title': True})

    assert fake_title_parser.requests[0].date_order == 'dmy'


def test_the_account_default_stays_month_first(funded_user):
    assert get_account(funded_user).user.date_format == 'mdy'
