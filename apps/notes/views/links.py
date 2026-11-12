"""The owner's share panel on the note page: the note's links, a form for a new one, editing and
revoking (issue #260, PR 4).

The panel is an htmx fragment opened by the note page's Share button (`GET share/`) and
re-rendered by each action; a refusal is the usual 422 with `Label: message` lines in `#share-msg`.
Without htmx every action is a plain POST that flashes a message and goes back to the note page
with the panel open (`?share=1`). Changes spend `NOTES_EDIT_USER`, validated first and charged
after. Another user's note or link is a 404.
"""

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.accounts.services.timezones import user_today
from apps.core.htmx import is_htmx

from ..forms import LinkForm
from ..models import LinkMode, Note, NotesSettings, ShareLink
from ..services import links
from .common import form_error_lines, limited, message_response, owned_note, validation_lines

SHARE_MSG = '#share-msg'


def note_url(note: Note) -> str:
    return reverse('notes:note', args=[note.pk])


def share_page_url(note: Note) -> str:
    """The note page with the share panel open (where a plain POST goes back to)."""
    return f'{note_url(note)}?share=1'


def panel_context(note: Note) -> dict:
    items = links.list_links(note)
    for link in items:
        if link.state == 'active':
            link.qr = links.qr_data_uri(link.url)
    active = sum(1 for link in items if link.state == 'active')
    config = NotesSettings.load()
    return {
        'note': note,
        'links': items,
        'active_count': active,
        'max_links': config.max_links_per_note,
        'can_share': not note.is_read_only,
        'has_active_link': active > 0,
        'today': user_today(note.user).isoformat(),
    }


def _panel(request: AuthenticatedHttpRequest, note: Note, *, status: int = 200) -> HttpResponse:
    return render(request, 'notes/partials/share_panel.html', panel_context(note), status=status)


def _done(request: AuthenticatedHttpRequest, note: Note, message: str) -> HttpResponse:
    """The panel again for htmx; a flash and the note page with the panel open otherwise."""
    if is_htmx(request):
        return _panel(request, note)
    messages.success(request, message)
    return redirect(share_page_url(note))


def _refused(request: AuthenticatedHttpRequest, note: Note, exc: ValidationError) -> HttpResponse:
    return _refused_lines(request, note, validation_lines(exc))


def _refused_lines(request: AuthenticatedHttpRequest, note: Note, lines: list[str]) -> HttpResponse:
    if is_htmx(request):
        return message_response(request, lines, retarget=SHARE_MSG)
    for line in lines:
        messages.error(request, line)
    return redirect(share_page_url(note))


def _owned_link(note: Note, link_id: UUID) -> ShareLink:
    link = get_object_or_404(ShareLink, pk=link_id, note=note)
    link.note = note  # the note (and its owner) are already loaded
    return link


@login_required
@require_GET
def share_panel(request: AuthenticatedHttpRequest, note_id: UUID) -> HttpResponse:
    """The panel (htmx). Opened directly it goes to the note page with the panel open."""
    note = owned_note(request, note_id, trashed=False)
    if not is_htmx(request):
        return redirect(share_page_url(note))
    return _panel(request, note)


@login_required
@require_POST
def share_create(request: AuthenticatedHttpRequest, note_id: UUID) -> HttpResponse:
    note = owned_note(request, note_id, trashed=False)
    form = LinkForm(request.POST)
    if not form.is_valid():
        return _refused_lines(request, note, form_error_lines(form))
    mode = form.cleaned_data['mode'] or LinkMode.READ
    label = form.cleaned_data['label']
    expires_on = form.expiry()
    try:  # validated first, charged after: a refused request spends no budget
        links.check_create(note, mode=mode, label=label, expires_on=expires_on)
    except ValidationError as exc:
        return _refused(request, note, exc)
    if (response := limited(request, 'NOTES_EDIT_USER', retarget=SHARE_MSG)) is not None:
        return response
    try:
        links.create_link(note, mode=mode, label=label, expires_on=expires_on)
    except ValidationError as exc:
        return _refused(request, note, exc)
    return _done(request, note, 'Link created.')


@login_required
@require_POST
def share_edit(request: AuthenticatedHttpRequest, note_id: UUID, link_id: UUID) -> HttpResponse:
    """Change a link's mode, label or expiry. A field missing from the POST is left alone; a
    posted empty `expires_on` removes the expiry."""
    note = owned_note(request, note_id, trashed=False)
    link = _owned_link(note, link_id)
    form = LinkForm(request.POST)
    if not form.is_valid():
        return _refused_lines(request, note, form_error_lines(form))
    changes: dict = {
        'mode': form.cleaned_data['mode'] or None,
        'label': form.cleaned_data['label'] if 'label' in request.POST else None,
    }
    if 'expires_on' in request.POST:
        expires_on = form.expiry()
        if expires_on is None:
            changes['clear_expiry'] = True
        else:
            changes['expires_on'] = expires_on
    try:
        links.check_update(link, **changes)
    except ValidationError as exc:
        return _refused(request, note, exc)
    if (response := limited(request, 'NOTES_EDIT_USER', retarget=SHARE_MSG)) is not None:
        return response
    try:
        links.update_link(link, **changes)
    except ValidationError as exc:
        return _refused(request, note, exc)
    return _done(request, note, 'Link updated.')


@login_required
@require_POST
def share_revoke(request: AuthenticatedHttpRequest, note_id: UUID, link_id: UUID) -> HttpResponse:
    """Revoke a link for good (from the confirmation modal, a plain POST)."""
    note = owned_note(request, note_id, trashed=False)
    link = _owned_link(note, link_id)
    try:
        links.require_shareable(note)
    except ValidationError as exc:
        return _refused(request, note, exc)
    if (response := limited(request, 'NOTES_EDIT_USER', retarget=SHARE_MSG)) is not None:
        return response
    try:
        links.revoke_link(link)
    except ValidationError as exc:
        return _refused(request, note, exc)
    return _done(request, note, 'Link revoked.')
