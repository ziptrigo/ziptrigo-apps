"""Vendors: the Vendors page (`/receipts/vendors/`), the vendor field's suggestions, and the
modals and actions behind them (rename, merge, mark as vetted, delete).

Every rule lives in `services.vendors`; these views gather input, call it and map `ValidationError`
to a 422. The page and its list partial follow the dashboard's pattern (a filter or tab change
re-fetches `vendor_list` into `#vendor-list` and `HX-Push-Url` keeps the page's own URL in the
address bar). The rename and merge dialogs are fragments fetched on demand into a `#vendor-modal`
host (on this page and on a receipt's page); their forms post back, a refused one is re-rendered in
place with status 422, and a successful one redirects (`HX-Redirect`) to the `next` page with a
flash message, so every count and name on it is fresh.
"""

from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.htmx import hx_redirect, is_htmx

from ..forms import (
    DEFAULT_VENDOR_TAB,
    VENDOR_PAGE_SIZE,
    VENDOR_TABS,
    ParsedVendorFilters,
    VendorNameForm,
    parse_vendor_filters,
)
from ..models import MAX_VENDOR_NAME_LENGTH, Receipt, ReceiptStatus, Vendor
from ..services import (
    VendorNameTaken,
    count_receipts,
    delete_vendor,
    list_vendors,
    merge_vendors,
    rename_vendor,
    set_vetted,
    suggest_vendors,
    teaches_extracted_name,
    vendor_counts,
    with_alias_names,
)
from ..services.vendors import clean_name
from .common import form_error_lines, message_response, owned_receipt, render_page

_ALSO_READ_AS_SHOWN = 3
_RENAME_NAMES_SHOWN = 3


# -- Shared helpers ---------------------------------------------------------------------------------


def _with_query(url: str, query: str) -> str:
    return f'{url}?{query}' if query else url


def _own_vendor(request: AuthenticatedHttpRequest, vendor_id: int) -> Vendor:
    """The requester's vendor, or a 404 (never a 403, so an id can't be probed)."""
    return get_object_or_404(Vendor, pk=vendor_id, user=request.user)


def _next_url(request: HttpRequest, default: str) -> str:
    """Where to go after an action: the `next` the page sent, if it is a page of this site."""
    target = request.POST.get('next') or request.GET.get('next') or ''
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return default


def _current_query(request: HttpRequest) -> QueryDict:
    """The query string of the page an htmx request was made from (`HX-Current-URL`): a bulk
    action refreshes the list as it was filtered and paged, without the page carrying the filters
    in every form."""
    query = urlparse(request.headers.get('HX-Current-URL', '')).query
    data = QueryDict(mutable=True)
    for key, values in parse_qs(query).items():
        data.setlist(key, values)
    return data


def _error(request: HttpRequest, lines: list[str] | str, *, retarget: str) -> HttpResponse:
    """A refused action: the message slot for htmx (422), a flash message and a redirect to the
    Vendors page otherwise."""
    if is_htmx(request):
        return message_response(request, lines, retarget=retarget)
    for line in [lines] if isinstance(lines, str) else lines:
        messages.error(request, line)
    return redirect(_next_url(request, reverse('receipts:vendors')))


def _highlight(name: str, term: str) -> tuple[str, str, str]:
    """`name` split around the first case-insensitive occurrence of `term` (for the bold match in
    the suggestions); the whole name first when it doesn't contain it."""
    index = name.casefold().find(term.casefold()) if term else -1
    if index < 0 or len(name.casefold()) != len(name):  # (casefolding can change the length)
        return name, '', ''
    return name[:index], name[index : index + len(term)], name[index + len(term) :]


# -- The Vendors page -------------------------------------------------------------------------------


def _list_context(request: AuthenticatedHttpRequest, parsed: ParsedVendorFilters, page) -> dict:
    page_obj = Paginator(
        list_vendors(request.user, status=parsed.status, q=parsed.q), VENDOR_PAGE_SIZE
    )
    page_obj = page_obj.get_page(page)
    vendors = list(page_obj.object_list)
    with_alias_names(vendors)
    for vendor in vendors:
        names = vendor.also_read_as
        vendor.also_shown = names[:_ALSO_READ_AS_SHOWN]
        vendor.also_more = max(len(names) - _ALSO_READ_AS_SHOWN, 0)

    counts = vendor_counts(request.user)
    tabs = []
    for key, label in VENDOR_TABS:
        query = parsed.query(status=None if key == DEFAULT_VENDOR_TAB else key)
        tabs.append(
            {
                'key': key,
                'label': label,
                'count': counts[key],
                'active': parsed.status == key,
                'href': _with_query(reverse('receipts:vendors'), query),
                'list_url': _with_query(reverse('receipts:vendor-list'), query),
            }
        )
    page_query = parsed.query(page=page_obj.number if page_obj.number > 1 else None)
    return {
        'vendors': vendors,
        'page_obj': page_obj,
        'parsed': parsed,
        'tabs': tabs,
        'counts': counts,
        'vendors_url': reverse('receipts:vendors'),
        'list_url': reverse('receipts:vendor-list'),
        'self_url': _with_query(reverse('receipts:vendors'), page_query),
        'prev_query': parsed.query(page=page_obj.previous_page_number())
        if page_obj.has_previous()
        else '',
        'next_query': parsed.query(page=page_obj.next_page_number()) if page_obj.has_next() else '',
        'page_query': page_query,
    }


@login_required
@require_GET
@ensure_csrf_cookie
def vendors_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    parsed = parse_vendor_filters(request.GET)
    return render_page(
        request, 'receipts/vendors.html', _list_context(request, parsed, request.GET.get('page'))
    )


@login_required
@require_GET
def vendor_list(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The list partial. Opened directly (not by htmx) it goes to the full page instead."""
    parsed = parse_vendor_filters(request.GET)
    page = request.GET.get('page')
    if not is_htmx(request):
        query = parsed.query(page=page)
        return redirect(_with_query(reverse('receipts:vendors'), query))

    context = _list_context(request, parsed, page)
    context['oob_tabs'] = True
    response = render(request, 'receipts/partials/vendor_list.html', context)
    response['HX-Push-Url'] = _with_query(reverse('receipts:vendors'), context['page_query'])
    return response


def _list_response(request: AuthenticatedHttpRequest, *, notice: str = '') -> HttpResponse:
    """The list as the user is looking at it (from `HX-Current-URL`), refreshed after an action
    on it; a flash message and a redirect to the page without htmx."""
    if not is_htmx(request):
        if notice:
            messages.success(request, notice)
        return redirect(_next_url(request, reverse('receipts:vendors')))
    data = _current_query(request)
    context = _list_context(request, parse_vendor_filters(data), data.get('page'))
    context['notice'] = notice
    context['oob_tabs'] = True
    return render(request, 'receipts/partials/vendor_list.html', context)


@login_required
@require_POST
def vendor_vet(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Mark vendors as vetted (`vetted=0` un-vets them): `ids` is the vendors, from the Vendors
    page's row menu and bulk bar. With `receipt` (the receipt page's "Mark as vetted") the answer
    is that receipt's vendor field instead of the list."""
    ids = request.POST.getlist('ids')
    vetted = request.POST.get('vetted', '1') != '0'
    receipt_id = request.POST.get('receipt')
    if receipt_id:
        # First, so that a 404 means nothing was vetted.
        try:
            UUID(receipt_id)
        except ValueError:
            raise Http404('Receipt not found') from None
        owned_receipt(request, receipt_id)
    retarget = '#receipt-form-msg' if receipt_id else '#vendor-msg'
    if not ids:
        return _error(request, 'Select at least one vendor.', retarget=retarget)
    try:
        changed = set_vetted(request.user, ids, vetted)
    except ValidationError as exc:
        return _error(request, exc.messages, retarget=retarget)

    if receipt_id:
        receipt = owned_receipt(request, receipt_id)  # (again: its vendor was just vetted)
        if not is_htmx(request):
            return redirect('receipts:detail', receipt_id=receipt.pk)
        return render(
            request,
            'receipts/partials/vendor_field.html',
            vendor_field_context(receipt),
        )
    verb = 'vetted' if vetted else 'not vetted'
    notice = f'Marked {changed} vendor{"" if changed == 1 else "s"} as {verb}.' if changed else ''
    return _list_response(request, notice=notice)


@login_required
@require_POST
def vendor_delete(request: AuthenticatedHttpRequest, vendor_id: int) -> HttpResponse:
    """Delete a vendor that has no receipts. A plain POST from the confirmation modal; the answer
    is a redirect with a flash message."""
    vendor = _own_vendor(request, vendor_id)
    name = vendor.name
    target = _next_url(request, reverse('receipts:vendors'))
    try:
        delete_vendor(vendor)
    except ValidationError as exc:
        for line in exc.messages:
            messages.error(request, line)
    else:
        messages.success(request, f'Deleted {name}.')
    return redirect(target)


# -- The vendor field on a receipt's page -------------------------------------------------------------


def vendor_field_context(receipt: Receipt) -> dict:
    """What `partials/vendor_field.html` needs for `receipt`: its vendor (with how many receipts
    it has), the name the AI read when it differs from the vendor's, the hint state, and whether
    moving the receipt to another vendor teaches that name (`teaches`: a vetted vendor called that
    keeps it, and the hint must not say otherwise)."""
    vendor = receipt.vendor_ref
    extracted = receipt.vendor_extracted
    differs = bool(extracted) and ' '.join(extracted.casefold().split()) != ' '.join(
        receipt.vendor.casefold().split()
    )
    settled = receipt.status in (ReceiptStatus.NEEDS_REVIEW, ReceiptStatus.PROCESSED)
    return {
        'receipt': receipt,
        'vendor': vendor,
        'vendor_receipt_count': count_receipts(vendor) if vendor is not None else 0,
        'read_as': extracted if differs else '',
        'teaches': teaches_extracted_name(receipt.user, extracted),
        'missing': set(receipt.missing_required_fields) if settled else set(),
        'trashed': receipt.deleted_at is not None,
        'detail_url': reverse('receipts:detail', args=[receipt.pk]),
    }


@login_required
@require_GET
def vendor_suggest(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The vendor field's list: the user's vendors matching what is typed (`q`, or `vendor`, the
    field's own name), vetted ones first, plus an entry for using the text as a new vendor."""
    term = clean_name(request.GET.get('q') or request.GET.get('vendor'))[:MAX_VENDOR_NAME_LENGTH]
    entries: list[dict[str, Any]] = [
        {'vendor': vendor, 'parts': _highlight(vendor.name, term)}
        for vendor in suggest_vendors(request.user, term)
    ]
    exists = bool(term) and Vendor.objects.filter(user=request.user, name__iexact=term).exists()
    return render(
        request,
        'receipts/partials/vendor_suggestions.html',
        {
            'vetted': [e for e in entries if e['vendor'].vetted],
            'unvetted': [e for e in entries if not e['vendor'].vetted],
            'term': term,
            'offer_new': bool(term) and not exists,
        },
    )


# -- Rename -----------------------------------------------------------------------------------------


def _rename_context(
    vendor: Vendor,
    *,
    next_url: str,
    name: str | None = None,
    vetted: bool = True,
    errors: list[str] | None = None,
    clash: Vendor | None = None,
) -> dict:
    """The rename form's context: the vendor's receipts and the names that keep matching it (the
    modal says so), and the clash with another vendor when there is one."""
    with_alias_names([vendor])
    known = [vendor.name, *vendor.also_read_as[:_RENAME_NAMES_SHOWN]]  # ty: ignore[unresolved-attribute]
    return {
        'vendor': vendor,
        'name': vendor.name if name is None else name,
        'vetted': vetted,
        'errors': errors or [],
        'clash': clash,
        'clash_count': count_receipts(clash) if clash is not None else 0,
        'receipt_count': count_receipts(vendor),
        'known_names': known,
        'next_url': next_url,
    }


@login_required
@require_http_methods(['GET', 'POST'])
def vendor_rename(request: AuthenticatedHttpRequest, vendor_id: int) -> HttpResponse:
    """GET: the rename dialog (a fragment). POST: rename the vendor; a name another vendor has
    comes back as a 422 offering to merge into it."""
    vendor = _own_vendor(request, vendor_id)
    default = reverse('receipts:vendors')
    next_url = _next_url(request, default)

    if request.method == 'GET':
        if not is_htmx(request):
            return redirect(default)
        return render(
            request,
            'receipts/partials/vendor_rename_modal.html',
            _rename_context(vendor, next_url=next_url),
        )

    form = VendorNameForm(request.POST)
    if not form.is_valid():
        context = _rename_context(
            vendor,
            next_url=next_url,
            name=request.POST.get('name', ''),
            vetted=bool(request.POST.get('vetted')),
            errors=form_error_lines(form),
        )
        return _rename_refused(request, context)
    try:
        rename_vendor(vendor, form.cleaned_data['name'], vetted=form.cleaned_data['vetted'])
    except VendorNameTaken as exc:
        context = _rename_context(
            vendor,
            next_url=next_url,
            name=form.cleaned_data['name'],
            vetted=form.cleaned_data['vetted'],
            clash=exc.other,
        )
        return _rename_refused(request, context)
    except ValidationError as exc:
        context = _rename_context(
            vendor,
            next_url=next_url,
            name=form.cleaned_data['name'],
            vetted=form.cleaned_data['vetted'],
            errors=list(exc.messages),
        )
        return _rename_refused(request, context)
    messages.success(request, f'Renamed to {vendor.name}.')
    return hx_redirect(request, next_url)


def _rename_refused(request: AuthenticatedHttpRequest, context: dict) -> HttpResponse:
    """The rename form again, with what was wrong (htmx swaps it in place); without htmx a flash
    message and a redirect back."""
    if is_htmx(request):
        return render(request, 'receipts/partials/vendor_rename_form.html', context, status=422)
    if context['clash'] is not None:
        messages.error(request, f'You already have a vendor called {context["clash"].name}.')
    for line in context['errors']:
        messages.error(request, line)
    return redirect(context['next_url'])


# -- Merge ------------------------------------------------------------------------------------------


def _ids(values: list[str]) -> list[int]:
    """The distinct integers in `values`, in order; anything that isn't one is dropped."""
    return list(dict.fromkeys(int(value) for value in values if value.strip().isdecimal()))


def _merge_context(
    request: AuthenticatedHttpRequest,
    vendors: list[Vendor],
    *,
    next_url: str,
    into: bool,
    keep_id: int | None = None,
    errors: list[str] | None = None,
) -> dict:
    """The merge dialog's context. Merging a selection asks which of `vendors` to keep (the most
    used vetted one by default); with `into`, `vendors` is the one vendor to merge and the dialog
    asks for another of the user's vendors to merge it into."""
    # (`receipt_count` is the annotation `list_vendors` puts on every vendor.)
    context: dict = {
        'next_url': next_url,
        'errors': errors or [],
        'into': into,
        'keep_id': keep_id,
        'total': sum(vendor.receipt_count for vendor in vendors),  # ty: ignore[unresolved-attribute]
    }
    if into:
        source = vendors[0]
        targets = list(list_vendors(request.user).exclude(pk=source.pk))
        targets.sort(key=lambda v: (not v.vetted, -v.receipt_count))
        context.update({'vendor': source, 'targets': targets})
    else:
        ordered = sorted(
            vendors,
            key=lambda v: (not v.vetted, -v.receipt_count, v.name.casefold()),  # ty: ignore[unresolved-attribute]
        )
        context['vendors'] = ordered
        if keep_id is None:
            context['keep_id'] = ordered[0].pk
    return context


@login_required
@require_http_methods(['GET', 'POST'])
def vendor_merge(request: AuthenticatedHttpRequest) -> HttpResponse:
    """GET: the merge dialog for `ids` (choose the name to keep), or for `from` (one vendor:
    choose the vendor to merge it into). POST (`keep`, `ids`, and `mode=into` for the second kind):
    merge the others into `keep`; a refusal comes back as the form again with a 422."""
    default = reverse('receipts:vendors')
    next_url = _next_url(request, default)

    if request.method == 'GET':
        if not is_htmx(request):
            return redirect(default)
        into = bool(request.GET.get('from'))
        ids = _ids(request.GET.getlist('from' if into else 'ids'))
        vendors = list(list_vendors(request.user).filter(pk__in=ids))
        if len(vendors) != len(ids) or len(vendors) < (1 if into else 2):
            return HttpResponse(status=404)
        return render(
            request,
            'receipts/partials/vendor_merge_modal.html',
            _merge_context(request, vendors, next_url=next_url, into=into),
        )

    into = request.POST.get('mode') == 'into'
    ids = _ids(request.POST.getlist('ids'))
    keep_ids = _ids([request.POST.get('keep', '')])
    keep_id = keep_ids[0] if keep_ids else None
    by_id = {
        vendor.pk: vendor for vendor in list_vendors(request.user).filter(pk__in={*ids, *keep_ids})
    }
    selected = [by_id[pk] for pk in ids if pk in by_id]
    others = [vendor for vendor in selected if vendor.pk != keep_id]

    error = ''
    if keep_id is None or keep_id not in by_id:
        error = 'Choose which vendor to keep.'
    elif len(selected) != len(ids):
        error = 'Unknown vendor.'
    elif not others:
        error = 'Choose at least two vendors to merge.'
    else:
        try:
            keep = merge_vendors(by_id[keep_id], others)
        except ValidationError as exc:
            error = exc.messages[0]
        else:
            total = sum(vendor.receipt_count for vendor in {*selected, by_id[keep_id]})
            messages.success(
                request,
                f'Merged {len(others)} vendor{"" if len(others) == 1 else "s"} into {keep.name}. '
                f'{total} receipt{"" if total == 1 else "s"} now show {keep.name}.',
            )
            return hx_redirect(request, next_url)

    if not is_htmx(request):
        messages.error(request, error)
        return redirect(next_url)
    shown = selected if selected and (into or len(selected) > 1) else []
    if not shown:
        return message_response(request, error, retarget='#vendor-msg')
    context = _merge_context(
        request, shown, next_url=next_url, into=into, keep_id=keep_id, errors=[error]
    )
    return render(request, 'receipts/partials/vendor_merge_form.html', context, status=422)
