"""To-do: `@label` and `#project` pills under a task title, in a real browser.

Typing `@` or `#` and a letter shows a pill per matching label or project; a click on one ticks the
label or chooses the project and takes the tag out of the title, and the task is created with it.
In the subtask form (no label controls) a click completes the tag in the title instead. Run on the
shared e2e server (`receipts_server`); the labels, project and tasks are made straight in the
database and removed after. Their names start with `zq`, which no other label of the e2e user does,
so one letter is enough to offer them.
"""

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

SETUP = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.todo.models import Label, Project, Task\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "Task.objects.filter(user=user, title__startswith='Zq ').delete()\n"
    "Project.objects.filter(user=user, name__startswith='Zq ').delete()\n"
    "Label.objects.filter(user=user, name__startswith='zq').delete()\n"
    "Project.objects.create(user=user, name='Zq garden', color='#5b8def')\n"
    "Label.objects.create(user=user, name='zqhome', color='#cc3366')\n"
    "Label.objects.create(user=user, name='zqhobby', color='#33aa66')\n"
    "parent = Task.objects.create(user=user, number=95001, title='Zq parent')\n"
    'print(parent.number)\n'
)
CLEANUP = (
    'import os\n'
    'from apps.todo.models import Label, Project, Task\n'
    "email = os.environ['E2E_EMAIL']\n"
    "Task.objects.filter(user__email=email, title__startswith='Zq ').delete()\n"
    "Project.objects.filter(user__email=email, name__startswith='Zq ').delete()\n"
    "Label.objects.filter(user__email=email, name__startswith='zq').delete()\n"
)
CHECK = (
    'import os\n'
    'from apps.todo.models import Task\n'
    "task = Task.objects.get(user__email=os.environ['E2E_EMAIL'], title='Zq call Bob')\n"
    "print(task.project.name if task.project else '-', sorted(l.name for l in task.labels.all()))\n"
)
CHECK_SUBTASK = (
    'import os\n'
    'from apps.todo.models import Task\n'
    "task = Task.objects.get(user__email=os.environ['E2E_EMAIL'], title='Zq sub')\n"
    'print(sorted(l.name for l in task.labels.all()))\n'
)


def _wired(page: Page, field: str) -> None:
    expect(page.locator(f'[data-title-tags]:has({field})')).to_have_attribute(
        'data-tags-ready', '1'
    )


def test_pills_suggest_and_confirm_labels_and_projects(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(SETUP)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/all/')
        _wired(page, '#quick-title')
        title = page.locator('#quick-title')
        pills = page.locator('#quick-tag-pills')
        home = pills.get_by_role('button', name='Add the label zqhome', exact=True)
        hobby = pills.get_by_role('button', name='Add the label zqhobby', exact=True)

        # One letter after `@` offers every label starting with it; more letters narrow it down.
        title.press_sequentially('Zq call Bob @z')
        expect(home).to_be_visible()
        expect(hobby).to_be_visible()
        title.press_sequentially('qhom')
        expect(hobby).to_be_hidden()

        # Escape hides the suggestions of that tag; typing more of it offers them again.
        title.press('Escape')
        expect(pills).to_be_hidden()
        title.press_sequentially('e')
        expect(home).to_be_visible()

        home.click()
        # The tag is gone; a space is left for the next word, with the caret after it.
        expect(title).to_have_value('Zq call Bob ')
        expect(pills).to_be_hidden()
        expect(page.locator('#quick-add-form [data-labels-summary]')).to_have_text('1 selected')

        # A label already ticked comes after the others and says so.
        title.press_sequentially('@zqho')
        expect(pills.locator('[data-tag-pill]').first).to_have_attribute(
            'aria-label', 'Add the label zqhobby'
        )
        expect(
            pills.get_by_role('button', name='Add the label zqhome (already added)')
        ).to_be_visible()
        title.fill('Zq call Bob ')

        # `#` and a word of the project's name: the Project select chooses it.
        title.press('End')
        title.press_sequentially('#gar')
        project_pill = pills.get_by_role('button', name='Put the task in the project Zq garden')
        expect(project_pill).to_be_visible()
        project_pill.click()
        expect(title).to_have_value('Zq call Bob ')
        expect(page.locator('#quick-add-form select[name="project"] option:checked')).to_have_text(
            'Zq garden'
        )

        # `#` and digits is a task reference, never a project.
        title.press_sequentially('#12')
        expect(pills).to_be_hidden()
        title.fill('Zq call Bob')

        page.click('#quick-add-form button[type="submit"]')
        expect(page.locator('#task-list').get_by_text('Zq call Bob')).to_be_visible()
        assert receipts_server.shell(CHECK).strip().splitlines()[-1] == "Zq garden ['zqhome']"
    finally:
        receipts_server.shell(CLEANUP)


def test_the_subtask_form_completes_the_tag(page: Page, receipts_server: ReceiptsServer):
    number = receipts_server.shell(SETUP).strip().splitlines()[-1]
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/{number}/')
        _wired(page, '#subtask-title')
        title = page.locator('#subtask-title')
        pills = page.locator('#subtask-tag-pills')

        title.press_sequentially('Zq sub @zqhom')
        pill = pills.get_by_role('button', name='Add the label zqhome', exact=True)
        expect(pill).to_be_visible()
        pill.click()
        # Completed in the title, which the server reads; an exact tag offers nothing more.
        expect(title).to_have_value('Zq sub @zqhome ')
        expect(pills).to_be_hidden()

        page.locator('#subtasks button[type="submit"]').click()
        expect(page.locator('#subtasks').get_by_text('Zq sub')).to_be_visible()
        assert receipts_server.shell(CHECK_SUBTASK).strip().splitlines()[-1] == "['zqhome']"
    finally:
        receipts_server.shell(CLEANUP)
