"""Projects (issue #314): `/todo/projects/` (the list: add, rename and recolour, reorder, archive,
delete) and `/todo/projects/<id>/` (one project: its description, progress and tasks), plus moving a
task between projects (`/todo/<number>/project/`).

The list is an htmx partial like the labels page (every add or edit re-renders `#projects`; a
refusal is a 422 in `#project-msg`). Archiving, unarchiving and deleting go through the shared
confirmation modal: plain POSTs that redirect with a flash message. Another user's project is a 404.
"""

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.accounts.services.timezones import user_today
from apps.core.htmx import hx_redirect, is_htmx

from ..forms import MoveForm, ProjectForm, SectionForm, TaskForm
from ..limits import MAX_SECTIONS_PER_PROJECT, PROJECT_PAGE_LIMIT
from ..models import DEFAULT_LABEL_COLOR, Label, Project, Section, Task
from ..services import SORTS, order_tasks, project_tasks, projects, sort_direction
from ..services.dates import DEFAULT_DIRECTIONS
from ..services.references import render_markdown, resolver_for_request
from .common import (
    announces_change,
    form_error_lines,
    limited,
    owned_task,
    projects_context,
    redirect_back,
    refuse,
    render_page,
    validation_lines,
)
from .lists import PAGE_SIZE, _query, decorate
from .tasks import after_action

SLOT = '#project-msg'
TABS = (('active', 'Active'), ('archived', 'Archived'))


def _list_url(tab: str = 'active') -> str:
    url = reverse('todo:projects')
    return f'{url}?tab=archived' if tab == 'archived' else url


def _project_url(project: Project) -> str:
    return reverse('todo:project', args=[project.pk])


def _owned(request: AuthenticatedHttpRequest, project_id: UUID) -> Project:
    return get_object_or_404(
        Project.objects.select_related('user'), pk=project_id, user=request.user
    )


def _tab(request: AuthenticatedHttpRequest) -> str:
    tab = request.POST.get('tab') or request.GET.get('tab') or 'active'
    return tab if tab in dict(TABS) else 'active'


def _list_context(request: AuthenticatedHttpRequest, tab: str) -> dict:
    everything = list(projects.list_projects(request.user, archived=None))
    active = [project for project in everything if not project.is_archived]
    archived = [project for project in everything if project.is_archived]
    return {
        'tab': tab,
        'tabs': [(key, label, len(active if key == 'active' else archived)) for key, label in TABS],
        'projects': archived if tab == 'archived' else active,
        'default_color': DEFAULT_LABEL_COLOR,
        'unfiled_count': projects.unfiled_count(request.user),
    }


def _list_response(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The refreshed list (and the sidebar's projects, out of band) for htmx; a redirect to the
    page otherwise."""
    tab = _tab(request)
    if not is_htmx(request):
        return redirect(_list_url(tab))
    context = _list_context(request, tab)
    context.update(projects_context(request.user), clear_msg=True, oob_sidebar=True)
    return render(request, 'todo/partials/project_list.html', context)


# -- The list ------------------------------------------------------------------------------------


@login_required
@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
def projects_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """GET: the page (`?tab=archived` for the archived ones). POST: add a project."""
    if request.method == 'POST':
        return _create(request)
    return render_page(
        request, 'todo/projects.html', _list_context(request, _tab(request)), nav='projects'
    )


def _create(request: AuthenticatedHttpRequest) -> HttpResponse:
    form = ProjectForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget=SLOT, fallback=_list_url())
    if (response := limited(request, 'TODO_EDIT_USER', retarget=SLOT)) is not None:
        return response
    try:
        projects.create_project(
            request.user,
            form.cleaned_data['name'],
            form.cleaned_data['color'],
            form.cleaned_data['description'],
        )
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=SLOT, fallback=_list_url())
    return _list_response(request)


@login_required
@require_POST
def project_edit(request: AuthenticatedHttpRequest, project_id: UUID) -> HttpResponse:
    """Rename, recolour or describe. Only what was posted changes. From the list (htmx) it answers
    the list; from the project's own page (`ctx=page`, a plain form) it redirects back there."""
    project = _owned(request, project_id)
    on_page = request.POST.get('ctx') == 'page'
    fallback = _project_url(project) if on_page else _list_url(_tab(request))
    slot = '#todo-msg' if on_page else SLOT
    form = ProjectForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget=slot, fallback=fallback)
    if (response := limited(request, 'TODO_EDIT_USER', retarget=slot)) is not None:
        return response
    posted = request.POST
    try:
        projects.update_project(
            project,
            name=form.cleaned_data['name'],
            color=form.cleaned_data['color'] if 'color' in posted else None,
            description=form.cleaned_data['description'] if 'description' in posted else None,
        )
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=slot, fallback=fallback)
    if on_page:
        messages.success(request, 'Project saved.')
        return hx_redirect(request, fallback)
    return _list_response(request)


@login_required
@require_POST
def project_reorder(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Write the order SortableJS posts (`ids[]`). Answers 204."""
    ids: list[UUID] = []
    for raw in request.POST.getlist('ids[]')[:500]:
        try:
            ids.append(UUID(raw))
        except ValueError:
            return refuse(request, 'Could not read the new order.', retarget=SLOT)
    if (response := limited(request, 'TODO_EDIT_USER', retarget=SLOT)) is not None:
        return response
    projects.reorder_projects(request.user, ids)
    return HttpResponse(status=204)


@login_required
@require_POST
def project_archive(request: AuthenticatedHttpRequest, project_id: UUID) -> HttpResponse:
    """Archive (from the confirmation modal, a plain POST): back to the page it was asked from."""
    project = _owned(request, project_id)
    if (response := limited(request, 'TODO_EDIT_USER')) is not None:
        return response
    if projects.archive_project(project):
        messages.success(
            request,
            f'Archived {project.name}. Its open tasks are off your lists and their reminders wait.',
        )
    return redirect_back(request, _list_url())


@login_required
@require_POST
def project_unarchive(request: AuthenticatedHttpRequest, project_id: UUID) -> HttpResponse:
    project = _owned(request, project_id)
    if (response := limited(request, 'TODO_EDIT_USER')) is not None:
        return response
    if projects.unarchive_project(project):
        messages.success(request, f'{project.name} is active again.')
    return redirect_back(request, _list_url())


@login_required
@require_POST
def project_delete(request: AuthenticatedHttpRequest, project_id: UUID) -> HttpResponse:
    project = _owned(request, project_id)
    if (response := limited(request, 'TODO_EDIT_USER')) is not None:
        return response
    name = project.name
    tab = 'archived' if project.is_archived else 'active'
    count = projects.delete_project(project)
    kept = (
        f' Its {count} task{"s" if count != 1 else ""} {"are" if count != 1 else "is"} kept, '
        'in no project.'
        if count
        else ''
    )
    messages.success(request, f'Deleted the project {name}.{kept}')
    return redirect(_list_url(tab))


# -- One project ---------------------------------------------------------------------------------


#: The sorts of a project's page: the hand-made order first (the default), then the shared ones.
PROJECT_SORT_DEFAULT = 'manual'
PROJECT_SORTS = {PROJECT_SORT_DEFAULT: 'Manual', **SORTS}


def _sort_links(sort: str, direction: str, show_closed: bool) -> list[dict]:
    """The "Sort by" bar of a project's page (`lists.sort_links`' shape). Manual is the default and
    has no direction; the active one of the others links to the opposite direction."""
    links = []
    for key, label in PROJECT_SORTS.items():
        active = key == sort
        if key == PROJECT_SORT_DEFAULT:
            wanted = ''
        else:
            flipped = 'asc' if direction == 'desc' else 'desc'
            wanted = flipped if active else DEFAULT_DIRECTIONS[key]
            wanted = wanted if wanted != DEFAULT_DIRECTIONS[key] else ''
        links.append(
            {
                'key': key,
                'label': label,
                'query': _query(
                    sort=key if key != PROJECT_SORT_DEFAULT else '',
                    dir=wanted,
                    closed='1' if show_closed else '',
                ),
                'active': active,
                'direction': direction if active and key != PROJECT_SORT_DEFAULT else '',
            }
        )
    return links


@login_required
@require_GET
@ensure_csrf_cookie
def project_page(request: AuthenticatedHttpRequest, project_id: UUID) -> HttpResponse:
    """The project's page: its open tasks grouped by section (the ones without a section first),
    in `?sort=` order (`manual`, the default: `Task.project_position`, set by dragging; or `date`,
    `priority`, `created`, `title` with `?dir=`). `?closed=1` adds the closed tasks in their own
    block below, 50 a page (`?page=`). The open tasks are not paged (sections and dragging work on
    the whole list); beyond `PROJECT_PAGE_LIMIT` the page points at the search."""
    project = projects.with_counts(_owned(request, project_id))
    user = request.user
    today = user_today(user)
    sort = request.GET.get('sort', '')
    sort = sort if sort in PROJECT_SORTS else PROJECT_SORT_DEFAULT
    manual = sort == PROJECT_SORT_DEFAULT
    direction = 'asc' if manual else sort_direction(sort, request.GET.get('dir'))
    show_closed = request.GET.get('closed') == '1'

    ordered = order_tasks(
        decorate(project_tasks(project)), 'project_manual' if manual else sort, direction
    )
    open_rows = list(ordered[: PROJECT_PAGE_LIMIT + 1])
    truncated = len(open_rows) > PROJECT_PAGE_LIMIT
    open_rows = open_rows[:PROJECT_PAGE_LIMIT]
    sections = projects.list_sections(project)
    known = {section.pk for section in sections}
    by_section: dict[UUID | None, list] = {}
    for task in open_rows:
        key = task.section_id if task.section_id in known else None
        by_section.setdefault(key, []).append(task)

    base = {
        'sort': sort if not manual else '',
        'dir': direction if not manual and direction != DEFAULT_DIRECTIONS[sort] else '',
    }
    context = {
        'project': project,
        'description_html': render_markdown(project.description, resolver_for_request(request))
        if project.description
        else '',
        'overdue_count': projects.overdue_count(project, today),
        'outside_blocked': 0 if project.is_archived else projects.outside_blocked_count(project),
        'unsectioned': by_section.get(None, []),
        'section_groups': [(section, by_section.get(section.pk, [])) for section in sections],
        'has_sections': bool(sections),
        'truncated': truncated,
        'page_limit': PROJECT_PAGE_LIMIT,
        'today': today,
        'view': 'project',
        'sort_links': _sort_links(sort, direction, show_closed),
        # Rows are dragged only in the hand-made order of a project that is not archived.
        # (nor while the list is cut at the cap: numbering only the rows shown would let the hidden
        # ones land among them)
        'sortable': manual and not project.is_archived and not truncated,
        'show_closed': show_closed,
        'closed_toggle_query': _query(**base, closed='' if show_closed else '1'),
        'page_url': _project_url(project),
        'default_color': DEFAULT_LABEL_COLOR,
        'current_project_id': project.pk,
    }
    if show_closed:
        closed = decorate(
            Task.objects.filter(
                project=project, deleted_at__isnull=True, completed_at__isnull=False
            )
        ).order_by('-completed_at', '-number')
        page_obj = Paginator(closed, PAGE_SIZE).get_page(request.GET.get('page'))
        context.update(
            {
                'closed_tasks': list(page_obj.object_list),
                'page_obj': page_obj,
                'prev_query': _query(**base, closed='1', page=str(page_obj.previous_page_number()))
                if page_obj.has_previous()
                else '',
                'next_query': _query(**base, closed='1', page=str(page_obj.next_page_number()))
                if page_obj.has_next()
                else '',
            }
        )
    if not project.is_archived:
        context.update(
            {
                'form': TaskForm(user=user),
                'labels': Label.objects.filter(user=user),
                'form_values': {},
                'selected_label_ids': [],
                'form_project_id': project.pk,
            }
        )
    return render_page(request, 'todo/project.html', context, nav='project')


# -- Sections ------------------------------------------------------------------------------------


def _section(project: Project, section_id: UUID) -> Section:
    return get_object_or_404(
        Section.objects.select_related('project'), pk=section_id, project=project
    )


def _section_done(
    request: AuthenticatedHttpRequest, project: Project, message: str
) -> HttpResponse:
    messages.success(request, message)
    return hx_redirect(request, _project_url(project))


@login_required
@require_POST
def section_add(request: AuthenticatedHttpRequest, project_id: UUID) -> HttpResponse:
    project = _owned(request, project_id)
    form = SectionForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), fallback=_project_url(project))
    if (response := limited(request, 'TODO_EDIT_USER')) is not None:
        return response
    try:
        section = projects.create_section(project, form.cleaned_data['name'])
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), fallback=_project_url(project))
    return _section_done(request, project, f'Added the section {section.name}.')


@login_required
@require_POST
def section_edit(
    request: AuthenticatedHttpRequest, project_id: UUID, section_id: UUID
) -> HttpResponse:
    project = _owned(request, project_id)
    section = _section(project, section_id)
    form = SectionForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), fallback=_project_url(project))
    if (response := limited(request, 'TODO_EDIT_USER')) is not None:
        return response
    try:
        projects.rename_section(section, form.cleaned_data['name'])
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), fallback=_project_url(project))
    return _section_done(request, project, 'Section renamed.')


@login_required
@require_POST
def section_delete(
    request: AuthenticatedHttpRequest, project_id: UUID, section_id: UUID
) -> HttpResponse:
    """Delete a section (from the confirmation modal, a plain POST); its tasks stay in the project."""
    project = _owned(request, project_id)
    section = _section(project, section_id)
    try:
        projects.require_active(project)  # a refusal spends no budget
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), fallback=_project_url(project))
    if (response := limited(request, 'TODO_EDIT_USER')) is not None:
        return response
    name = section.name
    try:
        count = projects.delete_section(section)
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), fallback=_project_url(project))
    kept = (
        f' Its {count} task{"s" if count != 1 else ""} stay{"" if count != 1 else "s"} in the project.'
        if count
        else ''
    )
    messages.success(request, f'Deleted the section {name}.{kept}')
    return redirect(_project_url(project))


@login_required
@require_POST
def section_reorder(request: AuthenticatedHttpRequest, project_id: UUID) -> HttpResponse:
    """Write the order of the project's sections (`ids[]`). Answers 204."""
    project = _owned(request, project_id)
    ids: list[UUID] = []
    for raw in request.POST.getlist('ids[]')[:MAX_SECTIONS_PER_PROJECT]:
        try:
            ids.append(UUID(raw))
        except ValueError:
            return refuse(request, 'Could not read the new order.')
    if (response := limited(request, 'TODO_EDIT_USER')) is not None:
        return response
    projects.reorder_sections(project, ids)
    return HttpResponse(status=204)


# -- Moving a task -------------------------------------------------------------------------------


@login_required
@require_POST
@announces_change
def task_move(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    """Put the task in a project (`project`: an id, or empty for "No project"). From the task page
    (`ctx=page`, htmx) it answers the panel; from a row's Move dialog (a plain POST) it redirects
    back with a message. Checked before the edit budget is charged."""
    task = owned_task(request, number)
    on_page = request.POST.get('ctx') == 'page'
    slot = '#task-msg' if on_page else '#todo-msg'
    fallback = reverse('todo:task', args=[task.number]) if on_page else ''
    form = MoveForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget=slot, fallback=fallback)
    # `section` (only when the request has it: the task page's Section select) puts the task in a
    # section of its project; a move to another project always leaves it in none.
    try:
        target = projects.check_move(task, form.cleaned_data['project'])
        # The Project and Section selects are one form, so a change of project also posts the
        # section of the project being left: it only counts while the project stays the same.
        same_project = (target.pk if target is not None else None) == task.project_id
        with_section = 'section' in request.POST and same_project
        if with_section:
            projects.clean_section(target, form.cleaned_data['section'])
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=slot, fallback=fallback)
    if (response := limited(request, 'TODO_EDIT_USER', retarget=slot)) is not None:
        return response
    try:
        with transaction.atomic():
            projects.move_task(task, target)
            if with_section:
                projects.set_section(task, form.cleaned_data['section'])
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=slot, fallback=fallback)
    if not is_htmx(request):
        where = f'to {target.name}' if target is not None else 'out of its project'
        messages.success(request, f'Moved {task.title} {where}.')
    return after_action(request, task)
