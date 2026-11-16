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
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.accounts.services.timezones import user_today
from apps.core.htmx import hx_redirect, is_htmx

from ..forms import MoveForm, ProjectForm, TaskForm
from ..models import DEFAULT_LABEL_COLOR, Label, Project
from ..services import SORT_DEFAULT, SORTS, order_tasks, project_tasks, projects, sort_direction
from ..services.dates import DEFAULT_DIRECTIONS
from ..services.references import render_markdown, resolver_for_request
from ..services.search import order_results
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
from .lists import PAGE_SIZE, _query, decorate, sort_links
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


@login_required
@require_GET
@ensure_csrf_cookie
def project_page(request: AuthenticatedHttpRequest, project_id: UUID) -> HttpResponse:
    """The project's page: `?sort=`, `?dir=`, `?closed=1` (closed tasks too, after the open ones)
    and `?page=`."""
    project = projects.with_counts(_owned(request, project_id))
    user = request.user
    today = user_today(user)
    sort = request.GET.get('sort', '')
    sort = sort if sort in SORTS else SORT_DEFAULT
    direction = sort_direction(sort, request.GET.get('dir'))
    show_closed = request.GET.get('closed') == '1'

    queryset = decorate(project_tasks(project, include_closed=show_closed))
    queryset = (
        order_results(queryset, sort, direction)
        if show_closed
        else order_tasks(queryset, sort, direction)
    )
    page_obj = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get('page'))
    base = {
        'sort': sort if sort != SORT_DEFAULT else '',
        'dir': direction if direction != DEFAULT_DIRECTIONS[sort] else '',
        'closed': '1' if show_closed else '',
    }
    closed_toggle = _query(**{**base, 'closed': '' if show_closed else '1'})
    links = sort_links(sort, direction, sorts=SORTS)
    for link in links:
        if show_closed:
            link['query'] = '&'.join(part for part in (link['query'], 'closed=1') if part)
    description_html = (
        render_markdown(project.description, resolver_for_request(request))
        if project.description
        else ''
    )
    context = {
        'project': project,
        'description_html': description_html,
        'overdue_count': projects.overdue_count(project, today),
        'outside_blocked': 0 if project.is_archived else projects.outside_blocked_count(project),
        'tasks': list(page_obj.object_list),
        'page_obj': page_obj,
        'today': today,
        'view': 'project',
        'sort_links': links,
        'show_closed': show_closed,
        'closed_toggle_query': closed_toggle,
        'page_url': _project_url(project),
        'prev_query': _query(**base, page=str(page_obj.previous_page_number()))
        if page_obj.has_previous()
        else '',
        'next_query': _query(**base, page=str(page_obj.next_page_number()))
        if page_obj.has_next()
        else '',
        'default_color': DEFAULT_LABEL_COLOR,
    }
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
    context['current_project_id'] = project.pk
    return render_page(request, 'todo/project.html', context, nav='project')


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
    try:
        target = projects.check_move(task, form.cleaned_data['project'])
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=slot, fallback=fallback)
    if (response := limited(request, 'TODO_EDIT_USER', retarget=slot)) is not None:
        return response
    try:
        projects.move_task(task, target)
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=slot, fallback=fallback)
    if not is_htmx(request):
        where = f'to {target.name}' if target is not None else 'out of its project'
        messages.success(request, f'Moved {task.title} {where}.')
    return after_action(request, task)
