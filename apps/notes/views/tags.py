"""`/notes/tags/`: the tags page -- add, rename and recolour, reorder, delete.

The list is an htmx partial (every action re-renders `#tags`; a refusal is a 422 in `#tag-msg`);
deleting goes through the shared confirmation modal, a plain POST that redirects with a flash
message. (To-do's labels page, with "tag" read as "label".)
"""

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.htmx import hx_redirect, is_htmx

from ..forms import TagForm
from ..limits import MAX_REORDER_IDS
from ..models import DEFAULT_TAG_COLOR, Tag
from ..services import create_tag, delete_tag, list_tags, reorder_tags, update_tag
from .common import form_error_lines, limited, refuse, render_page, validation_lines

SLOT = '#tag-msg'


def _page_url() -> str:
    return reverse('notes:tags')


def _list_response(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The refreshed list for htmx; a redirect to the page otherwise."""
    if not is_htmx(request):
        return redirect(_page_url())
    return render(
        request,
        'notes/partials/tag_list.html',
        {'tags': list_tags(request.user), 'default_color': DEFAULT_TAG_COLOR, 'clear_msg': True},
    )


@login_required
@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
def tags_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """GET: the page. POST: add a tag."""
    if request.method == 'POST':
        return _create(request)
    return render_page(
        request,
        'notes/tags.html',
        {'tags': list_tags(request.user), 'default_color': DEFAULT_TAG_COLOR},
        nav='tags',
    )


def _create(request: AuthenticatedHttpRequest) -> HttpResponse:
    form = TagForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget=SLOT, fallback=_page_url())
    if (response := limited(request, 'NOTES_EDIT_USER', retarget=SLOT)) is not None:
        return response
    try:
        create_tag(
            request.user,
            form.cleaned_data['name'],
            form.cleaned_data['color'],
            form.cleaned_data['description'],
        )
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=SLOT, fallback=_page_url())
    return _list_response(request)


@login_required
@require_POST
def tag_edit(request: AuthenticatedHttpRequest, tag_id: UUID) -> HttpResponse:
    """Rename or recolour. Only what was posted changes (an inline rename posts just the name)."""
    tag = get_object_or_404(Tag.objects.select_related('user'), pk=tag_id, user=request.user)
    form = TagForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget=SLOT, fallback=_page_url())
    if (response := limited(request, 'NOTES_EDIT_USER', retarget=SLOT)) is not None:
        return response
    posted = request.POST
    try:
        update_tag(
            tag,
            name=form.cleaned_data['name'],
            color=form.cleaned_data['color'] if 'color' in posted else None,
            description=form.cleaned_data['description'] if 'description' in posted else None,
        )
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=SLOT, fallback=_page_url())
    return _list_response(request)


@login_required
@require_POST
def tag_delete(request: AuthenticatedHttpRequest, tag_id: UUID) -> HttpResponse:
    tag = get_object_or_404(Tag, pk=tag_id, user=request.user)
    if (response := limited(request, 'NOTES_EDIT_USER', retarget=SLOT)) is not None:
        return response
    name = tag.name
    delete_tag(tag)
    messages.success(request, f'Deleted the tag {name}. Its notes keep their other tags.')
    return hx_redirect(request, _page_url())


@login_required
@require_POST
def tag_reorder(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Write the order SortableJS posts (`ids[]`). Answers 204."""
    if (response := limited(request, 'NOTES_EDIT_USER', retarget=SLOT)) is not None:
        return response
    ids: list[UUID] = []
    for raw in request.POST.getlist('ids[]'):
        try:
            ids.append(UUID(raw))
        except ValueError:
            return refuse(request, 'Could not read the new order.', retarget=SLOT)
    if len(ids) > MAX_REORDER_IDS:
        return refuse(request, 'Could not read the new order.', retarget=SLOT)
    reorder_tags(request.user, ids)
    return HttpResponse(status=204)
