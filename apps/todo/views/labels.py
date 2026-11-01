"""`/todo/labels/`: the labels page -- add, rename and recolour, reorder, delete.

The list is an htmx partial (every action re-renders `#labels`; a refusal is a 422 in
`#label-msg`); deleting goes through the shared confirmation modal, a plain POST that redirects with
a flash message.
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

from ..forms import LabelForm
from ..models import DEFAULT_LABEL_COLOR, Label
from ..services import create_label, delete_label, list_labels, reorder_labels, update_label
from .common import form_error_lines, limited, refuse, render_page, validation_lines

SLOT = '#label-msg'


def _page_url() -> str:
    return reverse('todo:labels')


def _list_response(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The refreshed list for htmx; a redirect to the page otherwise."""
    if not is_htmx(request):
        return redirect(_page_url())
    return render(
        request,
        'todo/partials/label_list.html',
        {
            'labels': list_labels(request.user),
            'default_color': DEFAULT_LABEL_COLOR,
            'clear_msg': True,
        },
    )


@login_required
@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
def labels_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """GET: the page. POST: add a label."""
    if request.method == 'POST':
        return _create(request)
    return render_page(
        request,
        'todo/labels.html',
        {'labels': list_labels(request.user), 'default_color': DEFAULT_LABEL_COLOR},
        nav='labels',
    )


def _create(request: AuthenticatedHttpRequest) -> HttpResponse:
    form = LabelForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget=SLOT, fallback=_page_url())
    if (response := limited(request, 'TODO_EDIT_USER', retarget=SLOT)) is not None:
        return response
    try:
        create_label(
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
def label_edit(request: AuthenticatedHttpRequest, label_id: UUID) -> HttpResponse:
    """Rename or recolour. Only what was posted changes (an inline rename posts just the name)."""
    label = get_object_or_404(Label.objects.select_related('user'), pk=label_id, user=request.user)
    form = LabelForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget=SLOT, fallback=_page_url())
    if (response := limited(request, 'TODO_EDIT_USER', retarget=SLOT)) is not None:
        return response
    posted = request.POST
    try:
        update_label(
            label,
            name=form.cleaned_data['name'],
            color=form.cleaned_data['color'] if 'color' in posted else None,
            description=form.cleaned_data['description'] if 'description' in posted else None,
        )
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=SLOT, fallback=_page_url())
    return _list_response(request)


@login_required
@require_POST
def label_delete(request: AuthenticatedHttpRequest, label_id: UUID) -> HttpResponse:
    label = get_object_or_404(Label, pk=label_id, user=request.user)
    if (response := limited(request, 'TODO_EDIT_USER', retarget=SLOT)) is not None:
        return response
    name = label.name
    delete_label(label)
    messages.success(request, f'Deleted the label {name}. Its tasks keep their other labels.')
    return hx_redirect(request, _page_url())


@login_required
@require_POST
def label_reorder(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Write the order SortableJS posts (`ids[]`). Answers 204."""
    if (response := limited(request, 'TODO_EDIT_USER', retarget=SLOT)) is not None:
        return response
    ids: list[UUID] = []
    for raw in request.POST.getlist('ids[]'):
        try:
            ids.append(UUID(raw))
        except ValueError:
            return refuse(request, 'Could not read the new order.', retarget=SLOT)
    reorder_labels(request.user, ids)
    return HttpResponse(status=204)
