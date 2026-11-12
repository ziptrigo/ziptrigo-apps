"""A note's history: the versions list, one version rendered and restoring one (owner only)."""

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest

from ..models import NoteVersion
from ..services import list_versions, restore_version
from .common import limited, owned_note, render_page


def _history_url(note) -> str:
    return reverse('notes:history', args=[note.pk])


@login_required
@require_GET
def history_list(request: AuthenticatedHttpRequest, note_id: UUID) -> HttpResponse:
    note = owned_note(request, note_id)
    versions = list(list_versions(note))
    return render_page(
        request,
        'notes/history.html',
        {
            'note': note,
            'versions': versions,
            'newest_number': versions[0].number if versions else None,
            'can_restore': not note.is_read_only,
        },
        nav='trash' if note.is_trashed else 'archived' if note.is_archived else 'notes',
    )


@login_required
@require_GET
def history_version(
    request: AuthenticatedHttpRequest, note_id: UUID, version_id: UUID
) -> HttpResponse:
    note = owned_note(request, note_id)
    version = get_object_or_404(
        NoteVersion.objects.select_related('editor'), pk=version_id, note=note
    )
    newest = list_versions(note).first()
    return render_page(
        request,
        'notes/history_version.html',
        {
            'note': note,
            'version': version,
            'is_current': newest is not None and newest.pk == version.pk,
            'can_restore': not note.is_read_only,
        },
        nav='trash' if note.is_trashed else 'archived' if note.is_archived else 'notes',
    )


@login_required
@require_POST
def history_restore(
    request: AuthenticatedHttpRequest, note_id: UUID, version_id: UUID
) -> HttpResponse:
    """Make a version the note's content again: a new save, so what it replaced stays in the
    history."""
    note = owned_note(request, note_id)
    version = get_object_or_404(NoteVersion, pk=version_id, note=note)
    if (response := limited(request, 'NOTES_EDIT_USER')) is not None:
        return response
    try:
        restore_version(note, version)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect(_history_url(note))
    messages.success(request, f'Restored version {version.number}.')
    return redirect(reverse('notes:note', args=[note.pk]))
