"""The shared note pages (issue #260, PR 4): `/n/<code>` shows a note to anyone who has the link,
read-only or editable; plus its raw Markdown, a download, a QR code, "Duplicate" for a signed-in
visitor, editing through an editable link and the "report this note" form.

**The code is the capability**, so every answer is `Cache-Control: no-store, private`,
`Referrer-Policy: strict-origin` and `X-Robots-Tag: noindex, nofollow` (and the pages carry a
`noindex` meta). **Every unavailable case** (unknown code, revoked, expired, the note in the Trash,
locked or taken down, or a `read` link asked for an edit page) renders the same standalone
`notes/unavailable.html` with status 404: it never says why. These paths are in
`TIMEZONE_MIDDLEWARE_SKIP_PREFIXES`, so the terms gate leaves them alone and they render in UTC and
the public date wording; the visitor may have no account at all.

Rate limits: `NOTES_VIEW_IP` is charged before the lookup on every GET (over it, a plain 429 page);
saving and ticking a box spend `NOTES_LINK_EDIT_IP` and `NOTES_LINK_EDIT_LINK`, the preview
`NOTES_LINK_PREVIEW_IP` (204 over it), reporting `NOTES_REPORT_IP` and `NOTES_REPORT_NOTE` (see
`report_note`), duplicating `NOTES_CREATE_USER`; all after validation (a refused request spends no budget).
"""

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.views import redirect_to_login
from django.contrib.sessions.backends.base import SessionBase
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.models import User
from apps.accounts.services import terms
from apps.billing.services import InsufficientCreditsError
from apps.core import ratelimit
from apps.core.htmx import hx_redirect, is_htmx
from apps.core.services.client_ip import client_ip

from ..forms import ChecklistForm, NoteForm, PreviewForm, ReportForm
from ..models import Note, ShareLink
from ..services import (
    CostChangedError,
    Editor,
    LinkUnavailableError,
    NoteConflictError,
    check_can_create,
    check_toggle,
    duplicate_shared,
    links,
    quote_create,
    reports,
    save_note,
    toggle_checklist,
)
from .common import (
    conflict_panel,
    credits_text,
    form_error_lines,
    out_of_credits_message,
    parse_cost,
    validation_lines,
)

EDITOR_MSG = '#editor-msg'
NOTE_MSG = '#note-msg'


class PublicHttpRequest(HttpRequest):
    """`HttpRequest` typed with the attributes the middleware add at runtime: a visitor has a
    `user` (anonymous, or signed in) and maybe a session."""

    session: SessionBase
    user: User | AnonymousUser


def signed_in_user(request: PublicHttpRequest) -> User | None:
    """The visitor's account, or `None` for someone who isn't signed in."""
    user = request.user
    return user if user.is_authenticated and isinstance(user, User) else None


SIGNED_IN_NOTICE = (
    "You're signed in: the note's owner will see your account name next to your changes."
)
ANONYMOUS_NOTICE = "Your changes are saved as 'Someone'."


def identity_notice(request: PublicHttpRequest, note: Note) -> str:
    """What the owner's history will say about the person who edits through the link, shown where
    they act (the editor and the checklist boxes): a signed-in visitor who isn't the owner is
    recorded by account, anyone else as "Someone". Nothing for the owner (their own edits)."""
    user = signed_in_user(request)
    if user is None:
        return ANONYMOUS_NOTICE
    return '' if user.pk == note.user_id else SIGNED_IN_NOTICE


# -- What every answer shares ------------------------------------------------------------------


def private(response: HttpResponse) -> HttpResponse:
    """Mark an answer about one shared note as not cacheable, not to be sent as a referrer and not
    for search engines: the code in the address is the capability.

    The policy is `strict-origin`, not `no-referrer`: nothing but the site's origin is ever sent as a
    `Referer` (never the path, which holds the code: not to another site, and not on a same-site
    request either, such as a link in the note to `/s/...` or another `/n/...`, whose address nginx
    would write into its access log), but with `no-referrer` a browser sends `Origin: null` on a
    same-origin form POST (the Fetch spec), which Django's CSRF check refuses, so the page's own
    plain forms (Duplicate, the report form without JavaScript) would all fail with a 403. With
    `strict-origin` the `Origin` header is still the page's own origin, so CSRF keeps working
    (verified by the Playwright test)."""
    response['Cache-Control'] = 'no-store, private'
    response['Referrer-Policy'] = 'strict-origin'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


def unavailable(request: HttpRequest) -> HttpResponse:
    """The one answer for every link that doesn't work, whatever the reason: a neutral 404 that
    never says why. Rendered from no context at all (standalone, like QR's), so a made-up code
    costs no session or user query and the bodies of every case are byte for byte the same."""
    return private(HttpResponse(render_to_string('notes/unavailable.html'), status=404))


def _became_unavailable(request: HttpRequest) -> HttpResponse:
    """For a POST: an htmx request can't swap a full page into a box sized for a form (and htmx
    doesn't swap a 404), so it reloads the page, which then renders the neutral answer."""
    if not is_htmx(request):
        return unavailable(request)
    response = HttpResponse(status=204)
    response['HX-Refresh'] = 'true'
    return private(response)


def _view_limited(request: HttpRequest) -> HttpResponse | None:
    """The plain 429 page when this IP is over `NOTES_VIEW_IP` (charged before the lookup)."""
    result = ratelimit.hit_ip(request, 'NOTES_VIEW_IP')
    if result.allowed:
        return None
    return private(ratelimit.page_response(request, result))


def _link_for(code: str, *, editable: bool = False) -> ShareLink | None:
    """The link that works now (`links.get_available`), and for `editable` also an `edit` one."""
    link = links.get_available(code)
    if link is not None and editable and not link.is_editable:
        return None
    return link


def _page_url(link: ShareLink) -> str:
    return reverse('n:page', args=[link.code])


# -- Reading -----------------------------------------------------------------------------------


def _page_context(
    request: PublicHttpRequest,
    link: ShareLink,
    *,
    report_form: ReportForm | None = None,
    reported: bool = False,
) -> dict:
    note = link.note
    user = signed_in_user(request)
    context: dict = {
        'note': note,
        'link': link,
        'tags': list(note.tags.all()),
        'checklist_url': reverse('n:checklist', args=[link.code]) if link.is_editable else None,
        'qr_data_uri': links.qr_data_uri(links.link_url(link)),
        'page_url': links.link_url(link),
        'report_form': report_form or ReportForm(),
        'reported': reported,
        'report_open': reported or report_form is not None,
        'duplicate_cost': quote_create(user) if user is not None else 0,
        'identity_notice': identity_notice(request, note) if link.is_editable else '',
    }
    return context


def render_page(
    request: PublicHttpRequest,
    link: ShareLink,
    *,
    report_form: ReportForm | None = None,
    reported: bool = False,
    status: int = 200,
) -> HttpResponse:
    """The shared page of a link that works, with the report box in the given state."""
    context = _page_context(request, link, report_form=report_form, reported=reported)
    return private(render(request, 'notes/public/page.html', context, status=status))


@require_GET
@ensure_csrf_cookie
def shared_page(request: PublicHttpRequest, code: str) -> HttpResponse:
    if (response := _view_limited(request)) is not None:
        return response
    link = _link_for(code)
    if link is None:
        return unavailable(request)
    return render_page(request, link)


@require_GET
def shared_raw(request: PublicHttpRequest, code: str) -> HttpResponse:
    """The Markdown as plain text."""
    if (response := _view_limited(request)) is not None:
        return response
    link = _link_for(code)
    if link is None:
        return unavailable(request)
    response = HttpResponse(link.note.content, content_type='text/plain; charset=utf-8')
    response['X-Content-Type-Options'] = 'nosniff'
    return private(response)


@require_GET
def shared_download(request: PublicHttpRequest, code: str) -> HttpResponse:
    """The Markdown as an attachment named after the title."""
    if (response := _view_limited(request)) is not None:
        return response
    link = _link_for(code)
    if link is None:
        return unavailable(request)
    note = link.note
    response = HttpResponse(note.content, content_type='text/markdown; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{slugify(note.title) or "note"}.md"'
    response['X-Content-Type-Options'] = 'nosniff'
    return private(response)


# -- Editing through an editable link ----------------------------------------------------------


def _edit_values(note: Note) -> dict:
    return {'title': note.title, 'content': note.content, 'base_version': note.version}


def _edit_context(
    request: PublicHttpRequest,
    link: ShareLink,
    values: dict,
    *,
    conflict: dict | None = None,
    error_lines: list[str] | None = None,
) -> dict:
    return {
        'note': link.note,
        'link': link,
        'values': values,
        'conflict': conflict,
        'error_lines': error_lines or [],
        'tags': [],
        'selected_tag_ids': set(),
        'action': reverse('n:edit', args=[link.code]),
        'cancel_url': _page_url(link),
        'preview_url': reverse('n:preview', args=[link.code]),
        'title_readonly': True,
        'identity_notice': identity_notice(request, link.note),
    }


def _edit_page(request: PublicHttpRequest, context: dict, *, status: int = 200) -> HttpResponse:
    return private(render(request, 'notes/public/edit.html', context, status=status))


def _edit_refusal(
    request: PublicHttpRequest, link: ShareLink, lines: list[str], *, values: dict
) -> HttpResponse:
    """A refused save: the lines in the editor's message slot for htmx, the whole editor again
    (with what was typed) otherwise."""
    if is_htmx(request):
        response = render(
            request, 'notes/partials/messages.html', {'messages_list': lines}, status=422
        )
        response['HX-Retarget'] = EDITOR_MSG
        response['HX-Reswap'] = 'innerHTML'
        return private(response)
    return _edit_page(
        request, _edit_context(request, link, values, error_lines=list(lines)), status=422
    )


def _edit_limited(
    request: PublicHttpRequest, link: ShareLink, retarget: str
) -> HttpResponse | None:
    """The 429 when the visitor's address or the link has had too many edits. Both rules are
    *peeked* first and charged (one hit each) only when both allow the request, so one refused by
    the link's ceiling doesn't also use up one of the visitor's own."""
    link_key = str(link.pk)
    for result in (
        ratelimit.peek_ip(request, 'NOTES_LINK_EDIT_IP'),
        ratelimit.peek_value(link_key, 'NOTES_LINK_EDIT_LINK'),
    ):
        if not result.allowed:
            return private(ratelimit.web_response(request, result, retarget=retarget))
    ratelimit.hit_ip(request, 'NOTES_LINK_EDIT_IP')
    ratelimit.hit_value(link_key, 'NOTES_LINK_EDIT_LINK')
    return None


@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
def shared_edit(request: PublicHttpRequest, code: str) -> HttpResponse:
    """GET: the editor, with the title read-only. POST: save the **content only**, or answer the
    conflict panel. Editable links only; a `read` link is the unavailable page."""
    if request.method == 'GET' and (response := _view_limited(request)) is not None:
        return response
    link = _link_for(code, editable=True)
    if link is None:
        return _became_unavailable(request) if request.method == 'POST' else unavailable(request)
    note = link.note
    if request.method == 'GET':
        return _edit_page(request, _edit_context(request, link, _edit_values(note)))

    form = NoteForm(request.POST)
    values = {
        'title': note.title,
        'content': form.data.get('content', ''),
        'base_version': (form.cleaned_data.get('base_version') if form.is_valid() else None)
        or note.version,
    }
    if not form.is_valid():
        return _edit_refusal(request, link, form_error_lines(form), values=values)
    base_version = form.cleaned_data['base_version']
    if base_version is None:
        return _edit_refusal(
            request,
            link,
            ['Version: Reload the editor: its version could not be read.'],
            values=values,
        )
    if (response := _edit_limited(request, link, EDITOR_MSG)) is not None:
        return response
    try:
        save_note(
            note,
            editor=Editor.for_user(note, signed_in_user(request), link),
            content=form.cleaned_data['content'],
            base_version=base_version,
            link=link,
            # "Save mine anyway" overwrites someone's newer text: that text stays in the history.
            coalesce=request.POST.get('overwrite') != '1',
        )
    except NoteConflictError as exc:
        conflict, values = conflict_panel(exc, values, compare_title=False)
        context = _edit_context(request, link, values, conflict=conflict)
        if not is_htmx(request):
            return _edit_page(request, context, status=422)
        response = render(request, 'notes/partials/editor_form.html', context, status=422)
        response['HX-Retarget'] = '#editor-form'
        response['HX-Reswap'] = 'outerHTML'
        return private(response)
    except LinkUnavailableError:
        return _became_unavailable(request)
    except ValidationError as exc:
        return _edit_refusal(request, link, validation_lines(exc), values=values)
    messages.success(request, 'Saved.')
    return private(hx_redirect(request, _page_url(link)))


@require_POST
def shared_preview(request: PublicHttpRequest, code: str) -> HttpResponse:
    """The live preview of an editable link's editor. Over `NOTES_LINK_PREVIEW_IP` it answers 204,
    so the last preview stays."""
    link = _link_for(code, editable=True)
    if link is None:
        return _became_unavailable(request)
    if not ratelimit.hit_ip(request, 'NOTES_LINK_PREVIEW_IP').allowed:
        return private(HttpResponse(status=204))
    form = PreviewForm(request.POST)
    content = form.cleaned_data['content'] if form.is_valid() else ''
    return private(render(request, 'notes/partials/preview.html', {'content': content}))


@require_POST
def shared_checklist(request: PublicHttpRequest, code: str) -> HttpResponse:
    """Tick or untick a checklist box through an editable link; answers the re-rendered body."""
    link = _link_for(code, editable=True)
    if link is None:
        return _became_unavailable(request)
    note = link.note
    form = ChecklistForm(request.POST)

    def refused(lines: list[str]) -> HttpResponse:
        response = render(
            request, 'notes/partials/messages.html', {'messages_list': lines}, status=422
        )
        response['HX-Retarget'] = NOTE_MSG
        response['HX-Reswap'] = 'innerHTML'
        return private(response)

    if not form.is_valid():
        return refused(form_error_lines(form))
    try:  # validated first, charged after: a refused request spends no budget
        check_toggle(note, form.cleaned_data['index'])
    except ValidationError as exc:
        return refused(validation_lines(exc))
    if (response := _edit_limited(request, link, NOTE_MSG)) is not None:
        return response
    try:
        note = toggle_checklist(
            note,
            form.cleaned_data['index'],
            form.cleaned_data['checked'],
            editor=Editor.for_user(note, signed_in_user(request), link),
            link=link,
        )
    except LinkUnavailableError:
        return _became_unavailable(request)
    except ValidationError as exc:
        return refused(validation_lines(exc))
    return private(
        render(
            request,
            'notes/partials/note_body.html',
            {'note': note, 'checklist_url': reverse('n:checklist', args=[link.code])},
        )
    )


# -- Duplicate ---------------------------------------------------------------------------------


@require_POST
def shared_duplicate(request: PublicHttpRequest, code: str) -> HttpResponse:
    """Copy the shared note into the visitor's own notes (title and content, no tags): a normal new
    note, free while a slot is open, else the creation price, confirmed first like everywhere
    (`confirm_cost`). Needs a session: without one the visitor is sent to sign in and back."""
    link = _link_for(code)
    if link is None:
        return unavailable(request)
    user = signed_in_user(request)
    if user is None:
        return private(redirect_to_login(_page_url(link)))
    if terms.needs_acceptance(user):
        # These pages are not terms-gated (a visitor may have no account), but making a note in an
        # account is, like everything else a signed-in user does.
        accept = reverse('accounts:terms-accept')
        return private(redirect(f'{accept}?{urlencode({"next": _page_url(link)})}'))
    back = _page_url(link)
    note = link.note
    try:  # validated first, charged after: a refused request spends no budget
        check_can_create(user, title=note.title, content=note.content)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return private(redirect(back))
    cost = quote_create(user)
    if cost and parse_cost(request.POST.get('confirm_cost')) != cost:
        messages.error(
            request,
            f'Duplicating this note costs {credits_text(cost)}. '
            'Confirm it from the Duplicate button to continue.',
        )
        return private(redirect(back))
    result = ratelimit.hit_user(user, 'NOTES_CREATE_USER')
    if not result.allowed:
        return private(ratelimit.page_response(request, result))
    try:
        # Whether the link still works is decided again inside, on a fresh read.
        copy = duplicate_shared(link, user, expected_cost=cost)
    except LinkUnavailableError:
        return _became_unavailable(request)
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
        return private(redirect(reverse('notes:note', args=[copy.pk])))
    return private(redirect(back))


# -- Reports -----------------------------------------------------------------------------------


def _report_answer(
    request: PublicHttpRequest,
    link: ShareLink,
    *,
    form: ReportForm | None = None,
    reported: bool = False,
    status: int = 200,
) -> HttpResponse:
    if not is_htmx(request):
        return render_page(request, link, report_form=form, reported=reported, status=status)
    response = render(
        request,
        'notes/public/report_box.html',
        {'link': link, 'report_form': form or ReportForm(), 'reported': reported},
        status=status,
    )
    return private(response)


@require_POST
def report_note(request: PublicHttpRequest, code: str) -> HttpResponse:
    """Report a shared note (`POST /n/<code>/report`): no login, no password -- anyone who has the
    link can. Only for a link that works now.

    `NOTES_REPORT_IP` is strict and counts every submission; `NOTES_REPORT_NOTE` is a looser
    per-note ceiling that is *peeked* first and *hit* only once a report was actually created
    (counting it up front would let a few addresses spend a note's whole budget on attempts that
    never become a report and lock every genuine reporter out behind them). The link is looked up
    before that per-note key is touched, so a made-up code creates none. An htmx request gets the
    report box back (a 422 with the errors, or the thank-you); a plain POST gets the whole page in
    the same state, so it works without JavaScript.
    """
    limited = ratelimit.hit_ip(request, 'NOTES_REPORT_IP')
    if not limited.allowed:
        return private(ratelimit.web_response(request, limited, retarget='#report-msg'))

    link = _link_for(code)
    if link is None:
        return _became_unavailable(request)

    note_key = str(link.note_id)
    limited = ratelimit.peek_value(note_key, 'NOTES_REPORT_NOTE')
    if not limited.allowed:
        return private(ratelimit.web_response(request, limited, retarget='#report-msg'))

    form = ReportForm(request.POST)
    if form.is_valid():
        try:
            reports.create_report(
                link,
                reason=form.cleaned_data['reason'],
                details=form.cleaned_data['details'],
                reporter_email=form.cleaned_data['reporter_email'],
                reporter_ip=client_ip(request),
            )
        except LinkUnavailableError:
            return _became_unavailable(request)
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
        else:
            # Only a report that was actually created counts against the per-note ceiling.
            ratelimit.hit_value(note_key, 'NOTES_REPORT_NOTE')
            return _report_answer(request, link, reported=True)
    return _report_answer(request, link, form=form, status=422)
