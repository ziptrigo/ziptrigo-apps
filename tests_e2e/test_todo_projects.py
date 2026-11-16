"""To-do projects in a real browser (issue #314): the project page's sections.

What only a browser shows: the confirmation modal opens from inside a section (a section whose
Alpine state shadowed the modal's `open` swallowed it), a section stays collapsed across a reload,
the Move dialog opens with the task's project selected, and a row moved to another section by the
keyboard-free path (the task page's Section select) lands there. Run on the shared e2e server
(`receipts_server`); the project is made straight in the database and removed after.
"""

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

SETUP = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.todo.models import Project, Section, Task\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "Project.objects.filter(user=user, name='Pj site').delete()\n"
    "Task.objects.filter(user=user, title__startswith='Pj ').delete()\n"
    "project = Project.objects.create(user=user, name='Pj site', color='#5b8def')\n"
    "design = Section.objects.create(project=project, name='Design', position=1)\n"
    "Section.objects.create(project=project, name='Content', position=2)\n"
    "Task.objects.create(user=user, number=94001, title='Pj loose', project=project)\n"
    "Task.objects.create(user=user, number=94002, title='Pj designed', project=project, "
    'section=design)\n'
    'print(project.pk)\n'
)
CLEANUP = (
    'import os\n'
    'from apps.todo.models import Project, Task\n'
    "Project.objects.filter(user__email=os.environ['E2E_EMAIL'], name='Pj site').delete()\n"
    "Task.objects.filter(user__email=os.environ['E2E_EMAIL'], title__startswith='Pj ').delete()\n"
)


def test_sections_on_the_project_page(page: Page, receipts_server: ReceiptsServer):
    project_id = receipts_server.shell(SETUP).strip().splitlines()[-1]
    url = f'{receipts_server.url}/todo/projects/{project_id}/'
    try:
        receipts_server.login(page)
        page.goto(url)
        design = page.locator('section', has=page.get_by_role('heading', name='Design'))
        expect(design.get_by_text('Pj designed')).to_be_visible()

        # The confirmation modal opens from a button inside a section (and from a row in it).
        design.get_by_role('button', name='Delete the section Design').click()
        modal = page.get_by_role('dialog')
        expect(modal).to_be_visible()
        expect(modal).to_contain_text('Its tasks are kept')
        page.keyboard.press('Escape')
        expect(modal).to_be_hidden()
        expect(design.get_by_text('Pj designed')).to_be_visible()  # the section did not collapse

        # Collapsing is remembered in this browser.
        design.get_by_role('button', name='Design', exact=True).click()
        expect(design.get_by_text('Pj designed')).to_be_hidden()
        page.reload()
        design = page.locator('section', has=page.get_by_role('heading', name='Design'))
        expect(design.get_by_text('Pj designed')).to_be_hidden()
        design.get_by_role('button', name='Design', exact=True).click()
        expect(design.get_by_text('Pj designed')).to_be_visible()

        # The Move dialog opens with the task's project selected, and Escape gives the focus back.
        opener = page.get_by_role('button', name='Move Pj loose to a project')
        opener.click()
        dialog = page.get_by_role('dialog', name='Move to project')
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role('radio', name='Pj site')).to_be_checked()
        page.keyboard.press('Escape')
        expect(dialog).to_be_hidden()
        expect(opener).to_be_focused()

        # The task page's Section select moves the task; the project page shows it there.
        page.goto(f'{receipts_server.url}/todo/94001/')
        page.select_option('#task-section', label='Content')
        expect(page.locator('#task-project-line')).to_contain_text('/ Content')
        page.goto(url)
        content = page.locator('section', has=page.get_by_role('heading', name='Content'))
        expect(content.get_by_text('Pj loose')).to_be_visible()
    finally:
        receipts_server.shell(CLEANUP)
