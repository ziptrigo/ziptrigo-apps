"""To-do: `@label` and `#project` pills under the quick-add title, in a real browser.

Typing `@` or `#` and a letter shows a pill per matching label or project; a click on one ticks the
label or chooses the project and takes the tag out of the title, and the task is created with it.
Run on the shared e2e server (`receipts_server`); the label and project are made straight in the
database and removed after.
"""

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

SETUP = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.todo.models import Label, Project, Task\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "Project.objects.filter(user=user, name__startswith='Tp ').delete()\n"
    "Label.objects.filter(user=user, name__startswith='tp').delete()\n"
    "Task.objects.filter(user=user, title__startswith='Tp call').delete()\n"
    "Project.objects.create(user=user, name='Tp garden', color='#5b8def')\n"
    "Label.objects.create(user=user, name='tphome', color='#cc3366')\n"
    "Label.objects.create(user=user, name='tphobby', color='#33aa66')\n"
)
CLEANUP = (
    'import os\n'
    'from apps.todo.models import Label, Project, Task\n'
    "email = os.environ['E2E_EMAIL']\n"
    "Task.objects.filter(user__email=email, title__startswith='Tp call').delete()\n"
    "Project.objects.filter(user__email=email, name__startswith='Tp ').delete()\n"
    "Label.objects.filter(user__email=email, name__startswith='tp').delete()\n"
)
CHECK = (
    'import os\n'
    'from apps.todo.models import Task\n'
    "task = Task.objects.get(user__email=os.environ['E2E_EMAIL'], title='Tp call Bob')\n"
    "print(task.project.name if task.project else '-', sorted(l.name for l in task.labels.all()))\n"
)


def test_pills_suggest_and_confirm_labels_and_projects(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(SETUP)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/all/')
        box = page.locator('[data-title-tags]:has(#quick-title)')
        expect(box).to_have_attribute('data-tags-ready', '1')
        title = page.locator('#quick-title')
        pills = page.locator('#quick-tag-pills')

        # One letter after `@` offers every label starting with it; more letters narrow it down.
        title.press_sequentially('Tp call Bob @tpho')
        expect(pills.get_by_role('button', name='Add the label tphome')).to_be_visible()
        expect(pills.get_by_role('button', name='Add the label tphobby')).to_be_visible()
        title.press_sequentially('m')
        expect(pills.get_by_role('button', name='Add the label tphobby')).to_be_hidden()

        pills.get_by_role('button', name='Add the label tphome').click()
        # The tag is gone; a space is left for the next word, with the caret after it.
        expect(title).to_have_value('Tp call Bob ')
        expect(pills).to_be_hidden()
        expect(page.locator('#quick-add-form [data-labels-summary]')).to_have_text('1 selected')

        # `#` and a word of the project's name: the Project select chooses it.
        title.press_sequentially('#gar')
        project_pill = pills.get_by_role('button', name='Put the task in the project Tp garden')
        expect(project_pill).to_be_visible()
        project_pill.click()
        expect(title).to_have_value('Tp call Bob ')
        expect(page.locator('#quick-add-form select[name="project"] option:checked')).to_have_text(
            'Tp garden'
        )

        # `#` and digits is a task reference, never a project.
        title.press_sequentially('#12')
        expect(pills).to_be_hidden()
        title.fill('Tp call Bob')

        page.click('#quick-add-form button[type="submit"]')
        expect(page.locator('#task-list').get_by_text('Tp call Bob')).to_be_visible()
        assert receipts_server.shell(CHECK).strip().splitlines()[-1] == "Tp garden ['tphome']"
    finally:
        receipts_server.shell(CLEANUP)
