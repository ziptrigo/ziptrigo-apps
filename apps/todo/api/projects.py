"""The project endpoints (`/api/todo/projects`, issue #314 part 3): the caller's projects, their
sections and the hand-made order of a project's tasks. Each one calls the service the website's
Projects pages use (`services.projects`) and adds no rule of its own. Every change spends
`TODO_EDIT_USER`, charged after the checks that need no write, so a refused request costs nothing.

A task's own project and section are set through the task endpoints (`project` and `section` of
`POST /tasks` and `PATCH /tasks/{number}`); `GET /tasks?project=` lists a project's tasks.
"""

from typing import Any
from uuid import UUID

from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from ..models import Project, Section
from ..schemas import (
    CountSchema,
    ProjectCreateSchema,
    ProjectOrderSchema,
    ProjectSchema,
    ProjectTaskOrderSchema,
    ProjectUpdateSchema,
    SectionNameSchema,
    SectionOrderSchema,
)
from ..services import projects as project_service
from ..services.labels import clean_color
from ..services.references import render_markdown
from .common import charge, choice, context_for, mapped_errors
from .router import router

ARCHIVED_CHOICES = ('false', 'true', 'all')


def _owned(request, project_id: UUID) -> Project:
    """The caller's project, or a 404 (never a 403)."""
    return get_object_or_404(
        Project.objects.select_related('user'), pk=project_id, user=request.auth
    )


def _section(project: Project, section_id: UUID) -> Section:
    """A section of `project`, or a 404 (also for a section of another project)."""
    return get_object_or_404(
        Section.objects.select_related('project'), pk=section_id, project=project
    )


def _data(request, project: Project, sections: list[Section]) -> dict[str, Any]:
    return {
        'id': project.pk,
        'name': project.name,
        'color': project.color,
        'description': project.description,
        'description_html': str(render_markdown(project.description, context_for(request).resolver))
        if project.description
        else '',
        'position': project.position,
        'archived_at': project.archived_at,
        'open_count': project.open_count,
        'closed_count': project.closed_count,
        'sections': [
            {'id': section.pk, 'name': section.name, 'position': section.position}
            for section in sections
        ],
        'created_at': project.created_at,
    }


def _many(request, projects: list[Project]) -> list[dict[str, Any]]:
    """The projects as JSON, their sections read in one query for all of them."""
    by_project: dict[UUID, list[Section]] = {}
    for section in Section.objects.filter(project__in=projects).order_by('position', 'created_at'):
        by_project.setdefault(section.project_id, []).append(section)
    return [_data(request, project, by_project.get(project.pk, [])) for project in projects]


def _one(request, project_id: UUID) -> dict[str, Any]:
    """The project again with its counts and sections (a 404 if it was deleted since)."""
    project = get_object_or_404(
        project_service.list_projects(request.auth, archived=None), pk=project_id
    )
    return _data(request, project, project_service.list_sections(project))


@router.get('/projects', response=list[ProjectSchema])
def list_projects(request, archived: str = 'false'):
    """The caller's projects in their order: the active ones (`archived=false`, the default), the
    archived ones (`true`) or both (`all`). Their counts and sections are read in two queries
    whatever their number (a description that mentions tasks adds a lookup per task number)."""
    which = choice('archived', archived.lower(), ARCHIVED_CHOICES)
    flag = {'false': False, 'true': True, 'all': None}[which]
    return _many(request, list(project_service.list_projects(request.auth, archived=flag)))


@router.post('/projects', response={201: ProjectSchema})
def create_project(request, payload: ProjectCreateSchema):
    """Add a project at the end of the list. A name another of your projects has (ignoring case),
    a bad colour, a description over 10,000 characters and the cap on projects are a 400."""
    with mapped_errors():
        # A refusal here spends no budget (the fields, a name already used, the cap).
        project_service.check_new_project(
            request.auth, payload.name, payload.color, payload.description
        )
        charge(request, 'TODO_EDIT_USER')
        project = project_service.create_project(
            request.auth, payload.name, payload.color, payload.description
        )
    return 201, _one(request, project.pk)


# Registered before the `{project_id}` routes, next to the other collection routes.
@router.post('/projects/reorder', response=list[ProjectSchema])
def reorder_projects(request, payload: ProjectOrderSchema):
    """Put the projects in the order given; ids that aren't yours are ignored. Answers the active
    projects in their new order."""
    if payload.ids:  # nothing to order spends nothing
        charge(request, 'TODO_EDIT_USER')
        project_service.reorder_projects(request.auth, payload.ids)
    return _many(request, list(project_service.list_projects(request.auth)))


@router.get('/projects/{project_id}', response=ProjectSchema)
def get_project(request, project_id: UUID):
    """One project, archived or not, with its counts and sections."""
    return _one(request, project_id)  # scoped to the caller: another user's is a 404


@router.patch('/projects/{project_id}', response=ProjectSchema)
def update_project(request, project_id: UUID, payload: ProjectUpdateSchema):
    """Rename, recolour or describe a project; only the fields present are applied. An archived
    project can be changed too. A body with nothing in it changes nothing and spends nothing."""
    project = _owned(request, project_id)
    changes = payload.model_dump(exclude_unset=True)
    if 'name' in changes and changes['name'] is None:
        raise HttpError(400, 'name: Cannot be null.')
    changes = {name: '' if value is None else value for name, value in changes.items()}
    if changes:
        with mapped_errors():
            if 'name' in changes:  # a refusal here spends no budget
                project_service.check_project_name(project, changes['name'])
            if 'color' in changes:
                clean_color(changes['color'])
            if 'description' in changes:
                project_service.clean_description(changes['description'])
            charge(request, 'TODO_EDIT_USER')
            project_service.update_project(project, **changes)
    return _one(request, project_id)


@router.delete('/projects/{project_id}', response={204: None})
def delete_project(request, project_id: UUID):
    """Delete a project. **Its tasks are kept**: they are then in no project (and no section), and
    the open tasks of an archived project come back to the lists."""
    project = _owned(request, project_id)
    charge(request, 'TODO_EDIT_USER')
    project_service.delete_project(project)
    return 204, None


@router.post('/projects/{project_id}/archive', response=ProjectSchema)
def archive_project(request, project_id: UUID):
    """Archive a project: nothing is deleted. Its open tasks leave the `today`, `upcoming` and
    `all` lists, are never overdue, and their reminders wait. Archiving an archived project
    answers it as it is and spends nothing."""
    project = _owned(request, project_id)
    if not project.is_archived:
        charge(request, 'TODO_EDIT_USER')
        project_service.archive_project(project)
    return _one(request, project_id)


@router.post('/projects/{project_id}/unarchive', response=ProjectSchema)
def unarchive_project(request, project_id: UUID):
    """Make an archived project active again: its open tasks are back on the lists, and each one
    that held reminders gets a single reminder for what it missed. A project that isn't archived
    is answered as it is and spends nothing."""
    project = _owned(request, project_id)
    if project.is_archived:
        charge(request, 'TODO_EDIT_USER')
        project_service.unarchive_project(project)
    return _one(request, project_id)


# -- Sections ------------------------------------------------------------------------------------


@router.post('/projects/{project_id}/sections', response={201: ProjectSchema})
def create_section(request, project_id: UUID, payload: SectionNameSchema):
    """Add a section at the end of the project's. A name the project already has (ignoring case),
    more than 50 sections, and an archived project are a 400. Answers the project."""
    project = _owned(request, project_id)
    with mapped_errors():
        project_service.check_new_section(project, payload.name)  # a refusal spends no budget
        charge(request, 'TODO_EDIT_USER')
        project_service.create_section(project, payload.name)
    return 201, _one(request, project_id)


@router.post('/projects/{project_id}/sections/reorder', response=ProjectSchema)
def reorder_sections(request, project_id: UUID, payload: SectionOrderSchema):
    """Put the project's sections in the order given; ids that aren't its own are ignored. A 400
    for an archived project."""
    project = _owned(request, project_id)
    with mapped_errors():
        project_service.require_active(project)  # an archived project's sections are frozen
        if payload.ids:
            charge(request, 'TODO_EDIT_USER')
            project_service.reorder_sections(project, payload.ids)
    return _one(request, project_id)


@router.patch('/projects/{project_id}/sections/{section_id}', response=ProjectSchema)
def rename_section(request, project_id: UUID, section_id: UUID, payload: SectionNameSchema):
    """Rename a section. Answers the project."""
    project = _owned(request, project_id)
    section = _section(project, section_id)
    with mapped_errors():
        project_service.check_section_name(section, payload.name)
        charge(request, 'TODO_EDIT_USER')
        project_service.rename_section(section, payload.name)
    return _one(request, project_id)


@router.delete('/projects/{project_id}/sections/{section_id}', response={204: None})
def delete_section(request, project_id: UUID, section_id: UUID):
    """Delete a section. **Its tasks are kept**: they stay in the project, in no section."""
    project = _owned(request, project_id)
    section = _section(project, section_id)
    with mapped_errors():
        project_service.require_active(project)
        charge(request, 'TODO_EDIT_USER')
        project_service.delete_section(section)
    return 204, None


@router.post('/projects/{project_id}/tasks/reorder', response=CountSchema)
def reorder_project_tasks(request, project_id: UUID, payload: ProjectTaskOrderSchema):
    """Write the hand-made order of one list of the project (see `ProjectTaskOrderSchema`; the ids
    sent swap the places they already hold, so a partial list never makes two tasks tie): what
    `GET /tasks?project=<id>&sort=manual` follows. `count` is how many tasks were positioned. An
    archived project, and a `section` that is not one of the project's, are a 400."""
    project = _owned(request, project_id)
    with mapped_errors():
        project_service.require_active(project)
        project_service.clean_section(project, payload.section)
        if not payload.ids:
            return {'count': 0}
        charge(request, 'TODO_EDIT_USER')
        count = project_service.reorder_tasks(
            request.auth, project, payload.section, list(payload.ids)
        )
    return {'count': count}
