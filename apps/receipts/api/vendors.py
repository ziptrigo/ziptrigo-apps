"""The vendor endpoints (`/api/receipts/vendors`): the user's own vendor list, which the AI's
vendor names are matched against. List, read, rename or vet, merge and delete, each calling the
service the Vendors page uses (`apps.receipts.services.vendors`), so the same rules apply.

Moving a *single receipt* to another vendor is `PATCH /receipts/{id}` (`vendor` or `vendor_id`);
the endpoints here act on the vendor and so on all its receipts. A vendor that isn't the caller's
is a 404, also inside a merge. A rename that clashes with another vendor is a 409 carrying that
vendor's id; see `common.py` for the other status codes.
"""

from ninja.errors import HttpError

from .. import services
from ..models import Vendor
from ..schemas import (
    VendorConflictSchema,
    VendorListSchema,
    VendorMergeSchema,
    VendorSchema,
    VendorUpdateSchema,
)
from .common import DEFAULT_LIMIT, clamp_page, mapped_errors
from .router import router


def _counted(user, vendor_id: int) -> Vendor:
    """The caller's vendor with its receipt statistics and alias names, or a 404."""
    vendor = services.list_vendors(user).filter(pk=vendor_id).first()
    if vendor is None:
        raise HttpError(404, 'Not Found')
    services.with_alias_names([vendor])
    return vendor


def _rename(vendor: Vendor, name: str, vetted: bool) -> services.VendorNameTaken | None:
    """Rename; the clash with another vendor is returned (the caller's 409), anything else the
    service refuses propagates as a `ValidationError`."""
    try:
        services.rename_vendor(vendor, name, vetted=vetted)
    except services.VendorNameTaken as exc:
        return exc
    return None


@router.get('/vendors', response=VendorListSchema)
def list_vendors(
    request,
    status: str = 'all',
    q: str = '',
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
):
    """List the caller's vendors by name.

    - `status`: `all` (default), `to_vet` (not vetted yet) or `vetted`.
    - `q`: matches a vendor's name or any of its aliases.
    - `limit` (1-100, default 20) and `offset` page the result; `count` is the total.

    `receipt_count` is the vendor's live receipts and `trashed_count` the ones in the Trash.
    """
    limit, offset = clamp_page(limit, offset)
    with mapped_errors():
        queryset = services.list_vendors(request.auth, status=status, q=q)
    page = list(queryset[offset : offset + limit])
    services.with_alias_names(page)
    return {'count': queryset.count(), 'limit': limit, 'offset': offset, 'results': page}


# Registered before the `{vendor_id}` routes: a different method and an `int` path converter, so
# `merge` is never read as an id either way, but it reads better next to the others.
@router.post('/vendors/merge', response=VendorSchema)
def merge_vendors(request, payload: VendorMergeSchema):
    """Merge the vendors in `merge` into `keep`: their receipts move to it (and show its name),
    the names they were known by become names `keep` is known by, they are deleted, and `keep` is
    vetted. Returns `keep`. A 404 if any of the ids isn't yours; a 400 if there is nothing to
    merge (no id besides `keep`)."""
    wanted = list(dict.fromkeys([payload.keep, *payload.merge]))
    found = {v.pk: v for v in services.list_vendors(request.auth).filter(pk__in=wanted)}
    if len(found) != len(wanted):
        raise HttpError(404, 'Not Found')
    with mapped_errors():
        services.merge_vendors(
            found[payload.keep], [v for pk, v in found.items() if pk != payload.keep]
        )
    return _counted(request.auth, payload.keep)


@router.get('/vendors/{int:vendor_id}', response=VendorSchema)
def get_vendor(request, vendor_id: int):
    return _counted(request.auth, vendor_id)


@router.patch(
    '/vendors/{int:vendor_id}',
    response={200: VendorSchema, 409: VendorConflictSchema},
)
def update_vendor(request, vendor_id: int, payload: VendorUpdateSchema):
    """Rename and/or vet a vendor. A `name` that another of your vendors already has (or an
    equivalent one: case, punctuation and `LLC`/`Inc.`-style endings don't count) is a 409 with
    `conflicting_vendor_id`, and nothing is changed: merge the two with `POST /vendors/merge`."""
    vendor = _counted(request.auth, vendor_id)
    changes = payload.dict(exclude_unset=True)
    name: str | None = changes.get('name')
    vetted: bool | None = changes.get('vetted')
    with mapped_errors():
        if name is not None:
            # Renaming is a confirmation: vetted afterwards unless the request says otherwise.
            clash = _rename(vendor, name, True if vetted is None else vetted)
            if clash is not None:
                return 409, {
                    'detail': clash.messages[0],
                    'conflicting_vendor_id': clash.other.pk,
                    'conflicting_vendor_name': clash.other.name,
                }
        elif vetted is not None:
            services.set_vetted(request.auth, [vendor.pk], vetted)
    return 200, _counted(request.auth, vendor_id)


@router.delete('/vendors/{int:vendor_id}', response={204: None})
def delete_vendor(request, vendor_id: int):
    """Delete a vendor that has no receipts, live or in the Trash (a 400 otherwise: merge it into
    another vendor instead)."""
    vendor = _counted(request.auth, vendor_id)
    with mapped_errors():
        services.delete_vendor(vendor)
    return 204, None
