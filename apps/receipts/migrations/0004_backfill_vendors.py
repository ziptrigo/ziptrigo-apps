"""Give every existing receipt a vendor (issue #123).

Per user, the receipts with a vendor are grouped by their vendor text (stripped, casefolded). Each
group becomes one `Vendor`:

* named by the group's most common spelling (ties: the alphabetically first), and the receipts that
  spell it differently get that name as their text, so `Receipt.vendor` equals the vendor's name;
* vetted if the user typed it on any receipt of the group (`'vendor'` in `user_edited_fields`);
* linked from every receipt of the group (`vendor_ref`), whose `vendor_extracted` is what the AI
  read (`ai_result['vendor']`, else empty).

Aliases are claimed by the vetted groups first, then by receipt count: first the group's own key,
then the keys of the names the AI read for its receipts, each only if no vendor has it yet. A group
whose own key was taken stays a separate vendor, which the user finds under "To vet" and can merge.
Nothing else changes on existing receipts: matching is not retroactive.

Reversing clears the links and the vendors (the vendors are created by this migration or after it).
"""

import re
import unicodedata
from collections import Counter

from django.db import migrations, transaction
from django.utils import timezone

USER_BATCH = 200
RECEIPT_BATCH = 500
MAX_VENDOR_NAME_LENGTH = 255

# A frozen copy of `apps.receipts.services.vendors.normalize_key` as it was when this migration was
# written. A migration must not import live app code (it changes, and drags the current models
# in), and the aliases written here have to mean the same thing whatever the matcher becomes.
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


def _group_receipts(Receipt, user_id):
    """`{casefolded text: [(id, text, edited by the user, what the AI read), ...]}`."""
    groups: dict[str, list[tuple]] = {}
    rows = (
        Receipt.objects.filter(user_id=user_id)
        .exclude(vendor='')
        .order_by('created_at', 'id')
        .values_list('id', 'vendor', 'user_edited_fields', 'ai_result__vendor')
    )
    for receipt_id, text, edited, ai_vendor in rows:
        text = ' '.join(text.split())
        if not text:
            continue
        read = ' '.join(ai_vendor.split()) if isinstance(ai_vendor, str) else ''
        groups.setdefault(text.casefold(), []).append(
            (receipt_id, text, 'vendor' in (edited or []), read[:255])
        )
    return groups


def _backfill_user(apps, user_id, now):
    Receipt = apps.get_model('receipts', 'Receipt')
    Vendor = apps.get_model('receipts', 'Vendor')
    VendorAlias = apps.get_model('receipts', 'VendorAlias')

    groups = []
    for members in _group_receipts(Receipt, user_id).values():
        spellings = Counter(text for _, text, _, _ in members)
        name = min(spellings, key=lambda text: (-spellings[text], text))
        groups.append((any(edited for _, _, edited, _ in members), name, members))
    # Vetted groups claim aliases first, then the ones with the most receipts.
    groups.sort(key=lambda group: (not group[0], -len(group[2]), group[1].casefold(), group[1]))

    claimed: set[str] = set()
    aliases = []
    changed = []
    for vetted, name, members in groups:
        vendor = Vendor.objects.create(user_id=user_id, name=name, vetted_at=now if vetted else None)
        source = 'user' if vetted else 'extracted'
        candidates = [(normalize_key(name), name, source)]
        candidates += [
            (normalize_key(read), read, 'extracted') for _, _, _, read in members if read
        ]
        for key, display, alias_source in candidates:
            if key and key not in claimed:
                claimed.add(key)
                aliases.append(
                    VendorAlias(
                        user_id=user_id, vendor=vendor, key=key, name=display, source=alias_source
                    )
                )
        for receipt_id, text, _, read in members:
            receipt = Receipt(pk=receipt_id)
            receipt.vendor_ref_id = vendor.pk
            receipt.vendor = name
            receipt.vendor_extracted = read
            changed.append(receipt)
    VendorAlias.objects.bulk_create(aliases, batch_size=RECEIPT_BATCH)
    for start in range(0, len(changed), RECEIPT_BATCH):
        Receipt.objects.bulk_update(
            changed[start : start + RECEIPT_BATCH],
            ['vendor_ref', 'vendor', 'vendor_extracted'],
        )


def _backfill_extracted_only(apps):
    """Receipts without a vendor (the AI read none, or the user cleared it) still get what the AI
    read, if anything."""
    Receipt = apps.get_model('receipts', 'Receipt')
    changed = []
    rows = (
        Receipt.objects.filter(vendor='', vendor_extracted='')
        .exclude(ai_result__isnull=True)
        .values_list('id', 'ai_result__vendor')
        .iterator(chunk_size=RECEIPT_BATCH)
    )
    for receipt_id, ai_vendor in rows:
        read = ' '.join(ai_vendor.split())[:255] if isinstance(ai_vendor, str) else ''
        if read:
            receipt = Receipt(pk=receipt_id)
            receipt.vendor_extracted = read
            changed.append(receipt)
        if len(changed) >= RECEIPT_BATCH:
            Receipt.objects.bulk_update(changed, ['vendor_extracted'])
            changed = []
    Receipt.objects.bulk_update(changed, ['vendor_extracted'])


def backfill_vendors(apps, schema_editor):
    Receipt = apps.get_model('receipts', 'Receipt')
    now = timezone.now()
    user_ids = list(
        Receipt.objects.exclude(vendor='')
        .order_by()
        .values_list('user_id', flat=True)
        .distinct()
    )
    for start in range(0, len(user_ids), USER_BATCH):
        for user_id in user_ids[start : start + USER_BATCH]:
            with transaction.atomic():
                _backfill_user(apps, user_id, now)
    _backfill_extracted_only(apps)


def clear_vendors(apps, schema_editor):
    Receipt = apps.get_model('receipts', 'Receipt')
    Vendor = apps.get_model('receipts', 'Vendor')
    Receipt.objects.exclude(vendor_ref=None).update(vendor_ref=None)
    Receipt.objects.exclude(vendor_extracted='').update(vendor_extracted='')
    Vendor.objects.all().delete()  # their aliases cascade


class Migration(migrations.Migration):
    dependencies = [
        ('receipts', '0003_vendors'),
    ]

    operations = [
        migrations.RunPython(backfill_vendors, clear_vendors),
    ]
