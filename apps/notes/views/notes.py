"""One note: the page, the editor (new and edit) with its live preview and the conflict panel, the
checklist, the tags popover and the actions (archive, Trash, restore, delete, duplicate, download)
and the manual order.

Forms follow the 422 convention (`Label: message` lines in a slot, or a panel swapped into the
form). **Credits are never spent without the person having seen the cost**: when creating,
duplicating or restoring would charge anything, the first POST charges nothing and answers with a
confirmation (the panel in the editor, the shared confirmation modal elsewhere), whose button
re-sends the form with `confirm_cost=<total>`. The view passes that total to the service as
`expected_cost`, which works the cost out again under the account lock and raises
`CostChangedError` when it differs, so a price or a free slot changed in between asks again.
"""

from datetime import timedelta
from math import ceil
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.billing.services import InsufficientCreditsError, get_balance
from apps.core import ratelimit
from apps.core.htmx import hx_redirect, is_htmx

from ..forms import ChecklistForm, NoteForm, PreviewForm
from ..limits import MAX_REORDER_IDS
from ..models import Note, NotesSettings, Tag
from ..services import (
    CostChangedError,
    Editor,
    NoteConflictError,
    archive,
    check_can_create,
    check_duplicable,
    check_tags,
    check_toggle,
    create_note,
    delete_forever,
    duplicate,
    links,
    quote_create,
    quote_restore,
    reorder,
    require_writable,
    restore,
    save_note,
    set_tags,
    toggle_checklist,
    trash,
    unarchive,
)
from ..services.notes import IN_TRASH_MESSAGE, LOCKED_MESSAGE, TAKEN_DOWN_MESSAGE
from .common import (
    conflict_panel,
    credits_text,
    form_error_lines,
    limited,
    message_response,
    out_of_credits_message,
    owned_note,
    parse_cost,
    redirect_back,
    refuse,
    render_page,
    validation_lines,
)

EDITOR_MSG = '#editor-msg'
NOTE_MSG = '#note-msg'


def note_url(note: Note) -> str:
    return reverse('notes:note', args=[note.pk])


# -- The page --------------------------------------------------------------------------------------


def _note_context(request: AuthenticatedHttpRequest, note: Note) -> dict:
    context: dict = {
        'note': note,
        'tags': list(Tag.objects.filter(user=request.user)),
        'selected_tag_ids': set(note.tags.values_list('pk', flat=True)),
        'can_edit': not note.is_read_only,
        'checklist_url': None if note.is_read_only else reverse('notes:checklist', args=[note.pk]),
        # Sharing: the Share button is for a note that isn't read-only; the panel opens on its own
        # after a plain-POST action (`?share=1`).
        'can_share': not note.is_read_only,
        'has_active_link': links.has_active_link(note),
        'open_share': request.GET.get('share') == '1' and not note.is_read_only,
        # A paid note in the Trash whose renewal fell due: restoring it pays that renewal.
        'renewal_overdue': bool(
            note.paid_through is not None and note.paid_through <= timezone.now()
        ),
    }
    if note.is_trashed:
        context['restore_cost'] = quote_restore(note)
        trash_days = NotesSettings.load().trash_days
        assert note.deleted_at is not None  # trashed
        purge_at = note.deleted_at + timedelta(days=trash_days)
        context['days_left'] = max(ceil((purge_at - timezone.now()).total_seconds() / 86400), 0)
    elif not note.is_taken_down:
        context['duplicate_cost'] = quote_create(request.user)
    return context


@login_required
@require_GET
@ensure_csrf_cookie
def note_page(request: AuthenticatedHttpRequest, note_id: UUID) -> HttpResponse:
    note = owned_note(request, note_id)
    nav = 'trash' if note.is_trashed else 'archived' if note.is_archived else 'notes'
    return render_page(request, 'notes/note.html', _note_context(request, note), nav=nav)


@login_required
@require_GET
def note_download(request: AuthenticatedHttpRequest, note_id: UUID) -> HttpResponse:
    """The Markdown as an attachment named after the title (also for a locked or trashed note)."""
    note = owned_note(request, note_id)
    response = HttpResponse(note.content, content_type='text/markdown; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{slugify(note.title) or "note"}.md"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


# -- The editor ------------------------------------------------------------------------------------


def _editor_context(
    request: AuthenticatedHttpRequest,
    note: Note | None,
    values: dict,
    *,
    conflict: dict | None = None,
    error_lines: list[str] | None = None,
) -> dict:
    return {
        'note': note,
        'values': values,
        'conflict': conflict,
        'error_lines': error_lines or [],
        'tags': list(Tag.objects.filter(user=request.user)),
        'selected_tag_ids': {str(pk) for pk in request.POST.getlist('tags')}
        if request.method == 'POST'
        else set(),
        'action': reverse('notes:edit', args=[note.pk]) if note else reverse('notes:new'),
        'cancel_url': note_url(note) if note else reverse('notes:list'),
    }


def _values(note: Note | None) -> dict:
    if note is None:
        return {'title': '', 'content': '', 'base_version': ''}
    return {'title': note.title, 'content': note.content, 'base_version': note.version}


def _posted_values(form: NoteForm, fallback_version) -> dict:
    data = form.cleaned_data if form.is_valid() else {}
    return {
        'title': form.data.get('title', ''),
        'content': form.data.get('content', ''),
        'base_version': data.get('base_version') or fallback_version,
    }


def _editor_page(
    request: AuthenticatedHttpRequest, context: dict, *, status: int = 200
) -> HttpResponse:
    note = context['note']
    nav = 'notes'
    if note is not None:
        nav = 'archived' if note.is_archived else 'notes'
    return render_page(request, 'notes/editor.html', context, nav=nav, status=status)


def _editor_refusal(
    request: AuthenticatedHttpRequest,
    note: Note | None,
    lines: list[str],
    *,
    values: dict,
    top_up: bool = False,
) -> HttpResponse:
    """A refused save: the lines in the editor's message slot for htmx; without htmx the whole
    editor again, with what was typed and the lines in the slot."""
    if is_htmx(request):
        return message_response(request, lines, retarget=EDITOR_MSG, top_up=top_up)
    context = _editor_context(request, note, values, error_lines=list(lines))
    context['top_up'] = top_up
    return _editor_page(request, context, status=422)


def _conflict_response(
    request: AuthenticatedHttpRequest, note: Note, exc: NoteConflictError, values: dict
) -> HttpResponse:
    """The conflict panel: the editor again with the person's own text kept, the current version
    beside it and "Save mine anyway" (the form's `base_version` is now the current one)."""
    conflict, values = conflict_panel(exc, values)
    context = _editor_context(request, note, values, conflict=conflict)
    if not is_htmx(request):
        return _editor_page(request, context, status=422)
    response = render(request, 'notes/partials/editor_form.html', context, status=422)
    response['HX-Retarget'] = '#editor-form'
    response['HX-Reswap'] = 'outerHTML'
    return response


def _cost_confirmation(request: AuthenticatedHttpRequest, cost: int, *, verb: str) -> HttpResponse:
    """The "this costs N credits" panel, as a 422 into `#cost-confirm` inside the editor."""
    config = NotesSettings.load()
    response = render(
        request,
        'notes/partials/cost_confirm.html',
        {
            'cost': cost,
            'balance': get_balance(request.user),
            'verb': verb,
            'renewal_price': config.renewal_price,
            'renewal_days': config.renewal_days,
        },
        status=422,
    )
    response['HX-Retarget'] = '#cost-confirm'
    response['HX-Reswap'] = 'innerHTML'
    return response


@login_required
@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
def note_new(request: AuthenticatedHttpRequest) -> HttpResponse:
    """GET: the empty editor. POST: create the note (asking first when it costs credits)."""
    if request.method == 'GET':
        return _editor_page(request, _editor_context(request, None, _values(None)))

    form = NoteForm(request.POST)
    values = _posted_values(form, '')
    if not form.is_valid():
        return _editor_refusal(request, None, form_error_lines(form), values=values)
    title = form.cleaned_data['title']
    content = form.cleaned_data['content']
    tag_ids = request.POST.getlist('tags')
    # The fields and the cap first: there is no point asking for credits for a note that can't be made.
    try:
        check_can_create(request.user, title=title, content=content, tags=tag_ids)
    except ValidationError as exc:
        return _editor_refusal(request, None, validation_lines(exc), values=values)
    cost = quote_create(request.user)
    if cost and parse_cost(form.cleaned_data['confirm_cost']) != cost:
        return _cost_confirmation(request, cost, verb='Create the note')

    if (response := limited(request, 'NOTES_CREATE_USER', retarget=EDITOR_MSG)) is not None:
        return response
    try:
        note = create_note(
            request.user, title=title, content=content, tags=tag_ids, expected_cost=cost
        )
    except CostChangedError as exc:
        return _cost_confirmation(request, exc.quote, verb='Create the note')
    except InsufficientCreditsError:
        return _editor_refusal(
            request,
            None,
            ["You don't have enough credits for this note."],
            values=values,
            top_up=True,
        )
    except ValidationError as exc:
        return _editor_refusal(request, None, validation_lines(exc), values=values)
    messages.success(request, 'Note created.')
    return hx_redirect(request, note_url(note))


@login_required
@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
def note_edit(request: AuthenticatedHttpRequest, note_id: UUID) -> HttpResponse:
    """GET: the editor. POST: save the title and content, or answer the conflict panel."""
    note = owned_note(request, note_id)
    if request.method == 'GET':
        if note.is_read_only:
            messages.error(
                request,
                IN_TRASH_MESSAGE
                if note.is_trashed
                else LOCKED_MESSAGE
                if note.is_locked
                else TAKEN_DOWN_MESSAGE,
            )
            return redirect(note_url(note))
        return _editor_page(request, _editor_context(request, note, _values(note)))

    form = NoteForm(request.POST)
    values = _posted_values(form, note.version)
    if not form.is_valid():
        return _editor_refusal(request, note, form_error_lines(form), values=values)
    base_version = form.cleaned_data['base_version']
    if base_version is None:
        return _editor_refusal(
            request,
            note,
            ['Version: Reload the editor: its version could not be read.'],
            values=values,
        )
    if (response := limited(request, 'NOTES_EDIT_USER', retarget=EDITOR_MSG)) is not None:
        return response
    try:
        save_note(
            note,
            editor=Editor.for_user(note, request.user),
            title=form.cleaned_data['title'],
            content=form.cleaned_data['content'],
            base_version=base_version,
            # "Save mine anyway" overwrites someone's newer text: that text stays in the history.
            coalesce=request.POST.get('overwrite') != '1',
        )
    except NoteConflictError as exc:
        return _conflict_response(request, note, exc, values)
    except ValidationError as exc:
        return _editor_refusal(request, note, validation_lines(exc), values=values)
    messages.success(request, 'Saved.')
    return hx_redirect(request, note_url(note))


@login_required
@require_POST
def note_preview(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The editor's live preview: the posted `content` rendered. Over the rate limit it answers
    204, so the last preview stays."""
    if not ratelimit.hit_user(request.user, 'NOTES_PREVIEW_USER').allowed:
        return HttpResponse(status=204)
    form = PreviewForm(request.POST)
    content = form.cleaned_data['content'] if form.is_valid() else ''
    return render(request, 'notes/partials/preview.html', {'content': content})


# -- The checklist and the tags --------------------------------------------------------------------


@login_required
@require_POST
def note_checklist(request: AuthenticatedHttpRequest, note_id: UUID) -> HttpResponse:
    """Tick or untick a checklist box of the content; answers the re-rendered body."""
    note = owned_note(request, note_id)
    form = ChecklistForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget=NOTE_MSG, fallback=note_url(note))
    try:  # validated first, charged after: a refused request spends no budget
        check_toggle(note, form.cleaned_data['index'])
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=NOTE_MSG, fallback=note_url(note))
    if (response := limited(request, 'NOTES_EDIT_USER', retarget=NOTE_MSG)) is not None:
        return response
    try:
        note = toggle_checklist(
            note,
            form.cleaned_data['index'],
            form.cleaned_data['checked'],
            editor=Editor.for_user(note, request.user),
        )
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=NOTE_MSG, fallback=note_url(note))
    if not is_htmx(request):
        return redirect(note_url(note))
    return render(
        request,
        'notes/partials/note_body.html',
        {
            'note': note,
            'checklist_url': None
            if note.is_read_only
            else reverse('notes:checklist', args=[note.pk]),
        },
    )


@login_required
@require_POST
def note_tags(request: AuthenticatedHttpRequest, note_id: UUID) -> HttpResponse:
    """Replace the note's tags with the ticked ones; answers the re-rendered tag chips."""
    note = owned_note(request, note_id, trashed=False)
    tag_ids = request.POST.getlist('tags')
    try:  # validated first, charged after: a refused request spends no budget
        check_tags(note, tag_ids)
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=NOTE_MSG, fallback=note_url(note))
    if (response := limited(request, 'NOTES_EDIT_USER', retarget=NOTE_MSG)) is not None:
        return response
    try:
        note = set_tags(note, tag_ids)
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=NOTE_MSG, fallback=note_url(note))
    if not is_htmx(request):
        return redirect(note_url(note))
    return render(request, 'notes/partials/note_tag_chips.html', {'note': note})


# -- Actions ---------------------------------------------------------------------------------------


def _act(
    request: AuthenticatedHttpRequest,
    note_id: UUID,
    action,
    done: str,
    *,
    trashed=False,
    check=None,
):
    """Look the note up, run the lock-free `check(note)` (if any), charge the edit budget, run
    `action(note)` and flash `done` (a refusal is flashed instead, and a request refused by the
    check spends no budget). Redirects back to where the request came from."""
    note = owned_note(request, note_id, trashed=trashed)
    if check is not None:
        try:
            check(note)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
            return redirect_back(request, note_url(note))
    if (response := limited(request, 'NOTES_EDIT_USER')) is not None:
        return response
    try:
        action(note)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    else:
        messages.success(request, done)
    return redirect_back(request, note_url(note))


@login_required
@require_POST
def note_archive(request: AuthenticatedHttpRequest, note_id: UUID) -> HttpResponse:
    return _act(request, note_id, archive, 'Note archived.', trashed=False, check=require_writable)


@login_required
@require_POST
def note_unarchive(request: AuthenticatedHttpRequest, note_id: UUID) -> HttpResponse:
    return _act(
        request,
        note_id,
        unarchive,
        'Note moved back to your notes.',
        trashed=False,
        check=require_writable,
    )


@login_required
@require_POST
def note_trash(request: AuthenticatedHttpRequest, note_id: UUID) -> HttpResponse:
    return _act(request, note_id, trash, 'Note moved to the Trash.', trashed=False)


@login_required
@require_POST
def note_delete(request: AuthenticatedHttpRequest, note_id: UUID) -> HttpResponse:
    """Delete a note in the Trash for good (from the confirmation modal)."""
    note = owned_note(request, note_id, trashed=True)
    if (response := limited(request, 'NOTES_EDIT_USER')) is not None:
        return response
    try:
        delete_forever(note)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect_back(request, reverse('notes:trash'), not_to=str(note.pk))
    messages.success(request, 'Note deleted for good.')
    # Its own page is gone now, so going "back" to it would be a 404.
    return redirect_back(request, reverse('notes:trash'), not_to=str(note.pk))


@login_required
@require_POST
def note_restore(request: AuthenticatedHttpRequest, note_id: UUID) -> HttpResponse:
    """Take a note out of the Trash. When that costs credits (a free note with no free slot left,
    or a renewal it skipped) the modal's `confirm_cost` has to match the cost."""
    note = owned_note(request, note_id, trashed=True)
    cost = quote_restore(note)
    if cost and parse_cost(request.POST.get('confirm_cost')) != cost:
        messages.error(
            request,
            f'Restoring this note costs {credits_text(cost)}. '
            'Confirm it from the Restore button to continue.',
        )
        return redirect_back(request, note_url(note))
    if (
        response := limited(request, 'NOTES_CREATE_USER' if cost else 'NOTES_EDIT_USER')
    ) is not None:
        return response
    try:
        restore(note, expected_cost=cost)
    except CostChangedError as exc:
        messages.error(
            request,
            f'Restoring this note now costs {credits_text(exc.quote)}. '
            'Confirm it from the Restore button to continue.',
        )
    except InsufficientCreditsError:
        messages.error(
            request, out_of_credits_message("You don't have enough credits to restore this note.")
        )
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    else:
        messages.success(request, 'Note restored.')
        return redirect(note_url(note))
    return redirect_back(request, note_url(note))


@login_required
@require_POST
def note_duplicate(request: AuthenticatedHttpRequest, note_id: UUID) -> HttpResponse:
    """A copy of the note ("<title> (copy)", no tags). A normal new note: free if a slot is open,
    else the creation price, confirmed first (`confirm_cost`)."""
    note = owned_note(request, note_id, trashed=False)
    try:
        check_duplicable(note)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect_back(request, note_url(note))
    cost = quote_create(request.user)
    if cost and parse_cost(request.POST.get('confirm_cost')) != cost:
        messages.error(
            request,
            f'Duplicating this note costs {credits_text(cost)}. '
            'Confirm it from the Duplicate button to continue.',
        )
        return redirect_back(request, note_url(note))
    if (response := limited(request, 'NOTES_CREATE_USER')) is not None:
        return response
    try:
        copy = duplicate(note, request.user, expected_cost=cost)
    except CostChangedError as exc:
        messages.error(
            request,
            f'Duplicating this note now costs {credits_text(exc.quote)}. '
            'Confirm it from the Duplicate button to continue.',
        )
    except InsufficientCreditsError:
        messages.error(
            request,
            out_of_credits_message("You don't have enough credits to duplicate this note."),
        )
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    else:
        messages.success(request, 'Note duplicated.')
        return redirect(note_url(copy))
    return redirect_back(request, note_url(note))


@login_required
@require_POST
def note_reorder(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Write the manual order SortableJS posts (`ids[]`). Answers 204; nothing on the page
    changes."""
    ids: list[UUID] = []
    for raw in request.POST.getlist('ids[]'):
        try:
            ids.append(UUID(raw))
        except ValueError:
            return refuse(request, 'Could not read the new order.')
    if len(ids) > MAX_REORDER_IDS:
        return refuse(request, 'Could not read the new order.')
    if (response := limited(request, 'NOTES_EDIT_USER', retarget='#notes-msg')) is not None:
        return response
    reorder(request.user, ids)
    return HttpResponse(status=204)
