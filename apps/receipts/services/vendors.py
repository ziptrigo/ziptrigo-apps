"""Vendors: matching what the AI read to a user's own vendor, and everything the user can do with
the vendor list (rename, vet, merge, delete).

**Matching** (`match_vendor`, run when a receipt is processed) is deterministic. Every name a vendor
is known by is stored as an alias under a normalized key (`normalize_key`: casefolded, accents and
punctuation gone, `&` read as "and", legal forms like LLC or GmbH dropped), and a key belongs to at
most one vendor per user. For a name the AI read, in the order of the issue:

1. a vetted vendor: the key is one of its aliases, else a near match on a vetted vendor's keys;
2. an unvetted vendor: the key is one of its aliases (exact only, so the pipeline never builds on
   its own guesses);
3. otherwise a new, unvetted vendor named as the AI read it.

**Teaching** is what makes the next receipt land on the right vendor: moving a receipt to another
vendor (`assign_vendor`) points the alias for the name the AI read on it at the chosen vendor, a
rename keeps the old name as an alias, and a merge moves the merged vendors' aliases. Nothing here
touches existing receipts except a rename or a merge, which rewrite `Receipt.vendor` (the text that
search, duplicates, the CSV and the API read) for the vendor's receipts in one `UPDATE`.

Callers pass objects they already fetched *for the requesting user* (a missing or foreign id is the
caller's 404); like `services.actions`, these functions raise Django's `ValidationError` for bad
input.
"""

import difflib
import re
import unicodedata
from collections.abc import Iterable

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, Exists, IntegerField, Max, OuterRef, Q, QuerySet, When
from django.db.models.deletion import RestrictedError
from django.db.models.functions import Lower
from django.utils import timezone

from apps.accounts.models import User

from ..models import (
    MAX_VENDOR_NAME_LENGTH,
    Receipt,
    Vendor,
    VendorAlias,
    VendorAliasSource,
)

#: Trailing words that say what kind of company it is, not which one. Dropped (repeatedly, but
#: never down to nothing) when a name becomes a key, so `ACME Industries, LLC` and `ACME
#: Industries` are the same vendor. Dots are removed before this (`L.L.C.` is `llc`).
LEGAL_FORMS = frozenset(
    {
        'ab',
        'ag',
        'aps',
        'as',
        'bv',
        'co',
        'company',
        'corp',
        'corporation',
        'eurl',
        'gmbh',
        'inc',
        'incorporated',
        'kg',
        'kk',
        'limited',
        'llc',
        'llp',
        'lda',
        'lp',
        'ltd',
        'ltda',
        'nv',
        'oy',
        'plc',
        'pllc',
        'pte',
        'pty',
        'sa',
        'sarl',
        'sas',
        'sl',
        'spa',
        'srl',
        'ug',
    }
)

#: A near match needs two keys at least this long (a short key differs by one letter from half the
#: dictionary) and a `difflib` ratio at least this high (`acme industries` / `acme industires`
#: is 0.93). Only ever tried against vetted vendors.
MIN_NEAR_LENGTH = 6
NEAR_RATIO = 0.92

#: How many vendors the vendor field's list offers.
SUGGESTION_LIMIT = 8

VENDOR_STATUSES = ('all', 'to_vet', 'vetted')


class VendorNameTaken(ValidationError):
    """Another vendor already has that name (or one with the same key). `other` is that vendor, so
    the caller can offer to merge into it."""

    def __init__(self, other: Vendor) -> None:
        super().__init__(f'You already have a vendor called {other.name}.')
        self.other = other


# -- Names and keys -------------------------------------------------------------------------------


def clean_name(name: str | None) -> str:
    """`name` with control characters removed and whitespace collapsed (what a vendor is called)."""
    text = ''.join(ch for ch in name or '' if unicodedata.category(ch) != 'Cc' or ch.isspace())
    return ' '.join(text.split())


def normalize_key(name: str) -> str:
    """The key `name` is matched under: NFKC, casefolded, Latin accents stripped, `&` read as
    "and", dots and apostrophes removed (`L.L.C.` is `llc`, `McDonald's` is `mcdonalds`), any other
    punctuation a word break, a leading "the" and the trailing legal forms (`LEGAL_FORMS`) dropped.
    Neither is ever dropped down to nothing: the key of `The Co` is `co`. A name with no letters or
    digits at all keys as its own casefolded text."""
    text = unicodedata.normalize('NFKC', name).casefold()
    # Only accents on an ASCII letter: stripping the marks of other scripts (a kana's dakuten) would
    # turn one name into another.
    kept: list[str] = []
    for char in unicodedata.normalize('NFKD', text):
        if unicodedata.combining(char) and kept and kept[-1].isascii():
            continue
        kept.append(char)
    text = ''.join(kept).replace('&', ' and ')
    text = re.sub(r"[.'’`´]", '', text)
    tokens = re.sub(r'[\W_]+', ' ', text).split()
    if len(tokens) > 1 and tokens[0] == 'the':
        tokens = tokens[1:]
    while len(tokens) > 1 and tokens[-1] in LEGAL_FORMS:
        tokens.pop()
    key = ' '.join(tokens) or ' '.join(unicodedata.normalize('NFKC', name).casefold().split())
    return key[:MAX_VENDOR_NAME_LENGTH]


def _check_name(name: str, label: str = 'The vendor name') -> str:
    """A name typed by the user, cleaned and checked."""
    cleaned = clean_name(name)
    if not cleaned:
        raise ValidationError('Enter a vendor name.')
    if len(cleaned) > MAX_VENDOR_NAME_LENGTH:
        raise ValidationError(f'{label} can be at most {MAX_VENDOR_NAME_LENGTH} characters.')
    return cleaned


# -- Finding and creating --------------------------------------------------------------------------


def _vendor_for_key(user: User, key: str) -> Vendor | None:
    alias = VendorAlias.objects.select_related('vendor').filter(user=user, key=key).first()
    return alias.vendor if alias is not None else None


def _ensure_alias(vendor: Vendor, key: str, name: str, source: str) -> bool:
    """Make `key` an alias of `vendor` if nobody has it yet. Returns whether `vendor` has it."""
    if not key:
        return False
    alias, _ = VendorAlias.objects.get_or_create(
        user_id=vendor.user_id,
        key=key,
        defaults={'vendor': vendor, 'name': name[:MAX_VENDOR_NAME_LENGTH], 'source': source},
    )
    return alias.vendor_id == vendor.pk


def _point_alias(vendor: Vendor, key: str, name: str, source: str) -> None:
    """Make `key` an alias of `vendor`, taking it over from whichever vendor had it."""
    if not key:
        return
    alias, created = VendorAlias.objects.get_or_create(
        user_id=vendor.user_id,
        key=key,
        defaults={'vendor': vendor, 'name': name[:MAX_VENDOR_NAME_LENGTH], 'source': source},
    )
    if not created and alias.vendor_id != vendor.pk:
        VendorAlias.objects.filter(pk=alias.pk).update(vendor=vendor, source=source)


def _find(user: User, name: str, key: str) -> Vendor | None:
    """The vendor `name` is: the one called that (ignoring case), else the one with an alias for
    its `key`. A vendor found by name gets the alias if nobody holds the key yet (a backfill leaves
    that out when another group claimed it first)."""
    vendor = Vendor.objects.filter(user=user, name__iexact=name).first()
    if vendor is not None:
        _ensure_alias(vendor, key, name, VendorAliasSource.EXTRACTED)
        return vendor
    return _vendor_for_key(user, key)


def _create(user: User, name: str, key: str, *, vetted: bool, source: str) -> Vendor:
    """A new vendor and its first alias, in a savepoint. Losing a race (another receipt of the same
    email creating the same vendor at the same moment, or taking the key) is not an error: the
    winner's vendor is used. No lock is taken for this, so there is no new lock ordering against
    intake and metering."""
    try:
        with transaction.atomic():
            vendor = Vendor.objects.create(
                user=user, name=name, vetted_at=timezone.now() if vetted else None
            )
            VendorAlias.objects.create(user=user, vendor=vendor, key=key, name=name, source=source)
        return vendor
    except IntegrityError:
        existing = _find(user, name, key)
        if existing is None:
            raise
        return existing


def _near_vetted_match(user: User, key: str) -> Vendor | None:
    """The one vetted vendor with a key within `NEAR_RATIO` of `key`, or `None` (none close
    enough, or several different vendors are: a guess between them is not worth making)."""
    if len(key) < MIN_NEAR_LENGTH:
        return None
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(key)
    best: dict[int, float] = {}
    rows = VendorAlias.objects.filter(user=user, vendor__vetted_at__isnull=False).values_list(
        'vendor_id', 'key'
    )
    for vendor_id, other in rows:
        if len(other) < MIN_NEAR_LENGTH or other == key:
            continue
        matcher.set_seq1(other)
        if matcher.real_quick_ratio() < NEAR_RATIO or matcher.quick_ratio() < NEAR_RATIO:
            continue
        ratio = matcher.ratio()
        if ratio >= NEAR_RATIO and ratio > best.get(vendor_id, 0.0):
            best[vendor_id] = ratio
    if len(best) != 1:
        return None
    return Vendor.objects.filter(pk=next(iter(best))).first()


def match_vendor(user: User, name: str | None) -> Vendor | None:
    """The vendor a receipt read as `name` belongs to (see the module docstring for the order),
    creating an unvetted one when nothing matches. `None` when no name was read."""
    cleaned = clean_name(name)[:MAX_VENDOR_NAME_LENGTH]
    if not cleaned:
        return None
    key = normalize_key(cleaned)
    exact = _find(user, cleaned, key)
    if exact is not None and exact.vetted_at is not None:
        return exact
    near = _near_vetted_match(user, key)
    if near is not None:
        return near
    if exact is not None:
        return exact
    return _create(user, cleaned, key, vetted=False, source=VendorAliasSource.EXTRACTED)


# -- Editing a receipt's vendor --------------------------------------------------------------------


def _vet(vendor: Vendor) -> None:
    if vendor.vetted_at is None:
        now = timezone.now()
        Vendor.objects.filter(pk=vendor.pk, vetted_at__isnull=True).update(
            vetted_at=now, updated_at=now
        )
        vendor.vetted_at = now


def assign_vendor(
    receipt: Receipt, *, vendor: Vendor | None = None, name: str | None = None
) -> Vendor:
    """Move `receipt` to a vendor the user chose: `vendor` (one of theirs) or `name`, which is
    resolved to the user's vendor with that name or key, else a new vendor is created. The chosen
    vendor becomes vetted (the user confirmed it), the receipt's `vendor_ref` and `vendor` are
    written, and the alias for the name the AI read on the receipt (`vendor_extracted`) is pointed
    at it, so the next receipt read that way lands there. Receipts already filed are not touched.
    A vendor the receipt leaves that is unvetted and has no receipts left is deleted.

    The caller holds the receipt's row lock (`update_receipt`) and saves nothing more than the
    fields this sets; the receipt's row is already updated here, because the old vendor can only
    go once it no longer references it."""
    if (vendor is None) == (name is None):
        raise ValueError('Pass either vendor or name.')
    with transaction.atomic():
        if vendor is None:
            cleaned = _check_name(name or '')
            key = normalize_key(cleaned)
            vendor = _find(receipt.user, cleaned, key) or _create(
                receipt.user, cleaned, key, vetted=True, source=VendorAliasSource.USER
            )
        elif vendor.user_id != receipt.user_id:
            raise ValidationError('Unknown vendor.')
        _vet(vendor)

        old_id = receipt.vendor_ref_id
        now = timezone.now()
        Receipt.objects.filter(pk=receipt.pk).update(
            vendor_ref=vendor, vendor=vendor.name, updated_at=now
        )
        receipt.vendor_ref = vendor
        receipt.vendor = vendor.name

        if receipt.vendor_extracted:
            _point_alias(
                vendor,
                normalize_key(receipt.vendor_extracted),
                receipt.vendor_extracted,
                VendorAliasSource.USER,
            )
        if old_id is not None and old_id != vendor.pk:
            drop_if_orphan(old_id)
    return vendor


def clear_vendor(receipt: Receipt) -> None:
    """Take the vendor off `receipt` (the user cleared the field). The vendor it leaves goes if it
    is unvetted and has no receipts left."""
    old_id = receipt.vendor_ref_id
    with transaction.atomic():
        Receipt.objects.filter(pk=receipt.pk).update(
            vendor_ref=None, vendor='', updated_at=timezone.now()
        )
        receipt.vendor_ref = None
        receipt.vendor = ''
        if old_id is not None:
            drop_if_orphan(old_id)


def drop_if_orphan(vendor_id: int | None) -> bool:
    """Delete the vendor if it is unvetted and no receipt, live or in the Trash, references it.
    Returns whether it was deleted. A vendor the user vetted is theirs and stays."""
    if vendor_id is None:
        return False
    vendor = Vendor.objects.filter(pk=vendor_id, vetted_at__isnull=True).first()
    if vendor is None or Receipt.objects.filter(vendor_ref_id=vendor_id).exists():
        return False
    try:
        with transaction.atomic():
            vendor.delete()
    except RestrictedError:  # a receipt was filed under it in the meantime
        return False
    return True


# -- The vendor list: rename, vet, merge, delete ---------------------------------------------------


def _clash(user: User, name: str, key: str, exclude_pk: int) -> Vendor | None:
    """Another vendor of the user that already has `name` or its key."""
    other = Vendor.objects.filter(user=user, name__iexact=name).exclude(pk=exclude_pk).first()
    if other is not None:
        return other
    alias = (
        VendorAlias.objects.select_related('vendor')
        .filter(user=user, key=key)
        .exclude(vendor_id=exclude_pk)
        .first()
    )
    return alias.vendor if alias is not None else None


def rename_vendor(vendor: Vendor, name: str, *, vetted: bool = True) -> Vendor:
    """Rename `vendor`: every receipt of it shows the new name. The old name stays one it is
    known by, and so does the new one, so receipts read under either keep matching it. `vetted`
    says whether the vendor is vetted afterwards (renaming is a confirmation, so by default it
    is).

    Raises:
        VendorNameTaken: another vendor has that name, or one with the same key (carrying that
            vendor, so the caller can offer to merge).
        ValidationError: the name is empty or too long.
    """
    cleaned = _check_name(name)
    key = normalize_key(cleaned)
    with transaction.atomic():
        locked = Vendor.objects.select_for_update().get(pk=vendor.pk)
        other = _clash(locked.user, cleaned, key, locked.pk)
        if other is not None:
            raise VendorNameTaken(other)

        now = timezone.now()
        locked.name = cleaned
        locked.vetted_at = (locked.vetted_at or now) if vetted else None
        try:
            with transaction.atomic():
                locked.save(update_fields=['name', 'vetted_at', 'updated_at'])
        except IntegrityError:  # another rename took the name between the check and the save
            other = _clash(locked.user, cleaned, key, locked.pk)
            if other is None:
                raise
            raise VendorNameTaken(other) from None
        if not _ensure_alias(locked, key, cleaned, VendorAliasSource.USER):
            other = _clash(locked.user, cleaned, key, locked.pk)
            if other is None:
                raise IntegrityError('Could not claim the vendor name')
            raise VendorNameTaken(other)
        Receipt.objects.filter(vendor_ref=locked).exclude(vendor=cleaned).update(
            vendor=cleaned, updated_at=now
        )
    vendor.name = locked.name
    vendor.vetted_at = locked.vetted_at
    return vendor


def set_vetted(user: User, ids: Iterable[int | str], vetted: bool) -> int:
    """Vet (or un-vet) the user's vendors with these ids. Returns how many changed.

    Raises:
        ValidationError: an id isn't one of the user's vendors.
    """
    try:
        wanted = {int(pk) for pk in ids}
    except TypeError, ValueError:
        raise ValidationError('Unknown vendor.') from None
    owned = Vendor.objects.filter(user=user, pk__in=wanted)
    if owned.count() != len(wanted):
        raise ValidationError('Unknown vendor.')
    now = timezone.now()
    if vetted:
        return owned.filter(vetted_at__isnull=True).update(vetted_at=now, updated_at=now)
    return owned.filter(vetted_at__isnull=False).update(vetted_at=None, updated_at=now)


def merge_vendors(keep: Vendor, others: Iterable[Vendor]) -> Vendor:
    """Merge `others` into `keep`: their receipts move to it (the link and the text), their
    aliases and their names become names `keep` is known by, they are deleted, and `keep` is
    vetted. Done in one transaction under row locks (taken in id order, so two merges can't
    deadlock).

    Raises:
        ValidationError: nothing to merge, a vendor isn't the same user's, or a receipt was filed
            under one of them while merging (try again).
    """
    other_ids = {other.pk for other in others} - {keep.pk}
    if not other_ids:
        raise ValidationError('Choose at least two vendors to merge.')
    with transaction.atomic():
        locked = {
            vendor.pk: vendor
            for vendor in Vendor.objects.select_for_update()
            .filter(user=keep.user, pk__in={keep.pk, *other_ids})
            .order_by('pk')
        }
        if len(locked) != len(other_ids) + 1:
            raise ValidationError('Unknown vendor.')
        target = locked[keep.pk]
        now = timezone.now()
        Receipt.objects.filter(vendor_ref_id__in=other_ids).update(
            vendor_ref=target, vendor=target.name, updated_at=now
        )
        VendorAlias.objects.filter(vendor_id__in=other_ids).update(
            vendor=target, source=VendorAliasSource.MERGE
        )
        for other_id in sorted(other_ids):
            other = locked[other_id]
            _ensure_alias(target, normalize_key(other.name), other.name, VendorAliasSource.MERGE)
        try:
            with transaction.atomic():
                Vendor.objects.filter(pk__in=other_ids).delete()
        except RestrictedError:
            raise ValidationError(
                'A receipt was just filed under one of these vendors. Try again.'
            ) from None
        if target.vetted_at is None:
            target.vetted_at = now
        target.save(update_fields=['vetted_at', 'updated_at'])
    keep.vetted_at = target.vetted_at
    return keep


def delete_vendor(vendor: Vendor) -> None:
    """Delete a vendor that has no receipts, live or in the Trash (its aliases go with it).

    Raises:
        ValidationError: it still has receipts; merge it into another vendor instead.
    """
    refusal = (
        f'{vendor.name} still has receipts. Merge it into another vendor instead of deleting it.'
    )
    if Receipt.objects.filter(vendor_ref=vendor).exists():
        raise ValidationError(refusal)
    try:
        with transaction.atomic():
            vendor.delete()
    except RestrictedError:
        raise ValidationError(refusal) from None


# -- Listing ---------------------------------------------------------------------------------------


def _with_receipt_stats(queryset: QuerySet) -> QuerySet:
    """Annotate vendors with how many live receipts they have (`receipt_count`), how many are in
    the Trash (`trashed_count`) and the latest `receipt_date` (`last_receipt_date`)."""
    return queryset.annotate(
        receipt_count=Count('receipts', filter=Q(receipts__deleted_at__isnull=True)),
        trashed_count=Count('receipts', filter=Q(receipts__deleted_at__isnull=False)),
        last_receipt_date=Max(
            'receipts__receipt_date', filter=Q(receipts__deleted_at__isnull=True)
        ),
    )


def _matching(queryset: QuerySet, term: str) -> QuerySet:
    """Vendors whose name, or the name of one of their aliases, contains `term`."""
    if not term:
        return queryset
    aliases = VendorAlias.objects.filter(vendor=OuterRef('pk'), name__icontains=term)
    return queryset.filter(Q(name__icontains=term) | Exists(aliases))


def list_vendors(user: User, *, status: str = 'all', q: str = '') -> QuerySet:
    """The user's vendors, by name, each with `receipt_count`, `trashed_count` and
    `last_receipt_date`. `status` is `all`, `to_vet` or `vetted`; `q` matches names and the names
    of aliases. Ordered explicitly: `Meta.ordering` is dropped from a query that aggregates.

    Raises:
        ValidationError: unknown `status`.
    """
    if status not in VENDOR_STATUSES:
        raise ValidationError(f'Unknown status: {status}.')
    queryset = Vendor.objects.filter(user=user)
    if status == 'to_vet':
        queryset = queryset.filter(vetted_at__isnull=True)
    elif status == 'vetted':
        queryset = queryset.filter(vetted_at__isnull=False)
    return _with_receipt_stats(_matching(queryset, q.strip())).order_by(Lower('name'), 'pk')


def vendor_counts(user: User) -> dict[str, int]:
    """The numbers on the Vendors page's tabs: `all`, `to_vet` and `vetted`."""
    return Vendor.objects.filter(user=user).aggregate(
        all=Count('pk'),
        to_vet=Count('pk', filter=Q(vetted_at__isnull=True)),
        vetted=Count('pk', filter=Q(vetted_at__isnull=False)),
    )


def suggest_vendors(user: User, q: str = '', limit: int = SUGGESTION_LIMIT) -> list[Vendor]:
    """Up to `limit` of the user's vendors whose name or an alias contains `q` (all of them when
    `q` is empty) for the vendor field's list: vetted ones first, then the one with the most
    receipts."""
    vetted_first = Case(
        When(vetted_at__isnull=False, then=0), default=1, output_field=IntegerField()
    )
    queryset = _with_receipt_stats(_matching(Vendor.objects.filter(user=user), q.strip()))
    return list(queryset.order_by(vetted_first, '-receipt_count', Lower('name'), 'pk')[:limit])


def with_alias_names(vendors: Iterable[Vendor]) -> None:
    """Set `also_read_as` on each vendor: the names it is known by other than its own, oldest
    first, for the Vendors page ("Also read as ..."). One query for all of them."""
    by_id = {vendor.pk: vendor for vendor in vendors}
    for vendor in by_id.values():
        vendor.also_read_as = []  # ty: ignore[unresolved-attribute]
    own_keys = {vendor.pk: normalize_key(vendor.name) for vendor in by_id.values()}
    for alias in VendorAlias.objects.filter(vendor_id__in=by_id).order_by('created_at', 'pk'):
        if alias.key != own_keys[alias.vendor_id]:
            by_id[alias.vendor_id].also_read_as.append(alias.name)  # ty: ignore[unresolved-attribute]


def count_receipts(vendor: Vendor) -> int:
    """How many live receipts are filed under `vendor`."""
    return Receipt.objects.filter(vendor_ref=vendor, uploaded=True, deleted_at__isnull=True).count()
