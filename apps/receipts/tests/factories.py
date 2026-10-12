"""Helpers that build receipts in the states the tests need."""

import uuid

from django.utils import timezone

from apps.accounts.models import User

from ..models import Receipt, ReceiptSource, ReceiptStatus, Vendor, VendorAlias, VendorAliasSource
from ..services.accounts import add_stored_bytes, get_account
from ..services.documents import sniff_content_type
from ..services.storage import file_key
from ..services.vendors import normalize_key
from . import samples
from .fakes import FakeReceiptsStorage


def create_receipt(
    user: User,
    storage: FakeReceiptsStorage,
    *,
    data: bytes | None = None,
    store: bool = True,
    count_bytes: bool = True,
    **fields,
) -> Receipt:
    """A completed (uploaded) receipt whose file is in `storage`. `data` defaults to a small JPEG;
    the content type is sniffed from it unless given. Counts the bytes against the account like a
    real upload would, unless `count_bytes=False`."""
    get_account(user)
    data = samples.jpeg_bytes() if data is None else data
    receipt_id = fields.pop('id', None) or uuid.uuid4()
    key = fields.pop('file_key') if 'file_key' in fields else file_key(user.pk, receipt_id)
    defaults = {
        'source': ReceiptSource.UPLOAD,
        'original_filename': 'receipt.jpg',
        'content_type': sniff_content_type(data, allow_text=True) or 'image/jpeg',
        'size_bytes': len(data),
        'status': ReceiptStatus.RECEIVED,
        'uploaded': True,
    }
    defaults.update(fields)
    receipt = Receipt.objects.create(id=receipt_id, user=user, file_key=key, **defaults)
    if store and key:
        storage.seed(key, data)
    if count_bytes:
        add_stored_bytes(user.pk, len(data))
    return receipt


def create_inbound_email(user: User, **fields):
    """An accepted `InboundEmail` (with its `InboundMessage`) for `user`."""
    from ..models import InboundEmail, InboundMessage

    message = InboundMessage.objects.create(s3_key=f'receipts/inbound/{uuid.uuid4()}')
    defaults = {
        'recipient': 'receipts+abcd2345@ziptrigo.com',
        'from_address': 'orders@staples.com',
        'subject': 'Fwd: October invoices',
    }
    defaults.update(fields)
    return InboundEmail.objects.create(user=user, message=message, **defaults)


def make_user(**fields) -> User:
    """A confirmed, active user without a password (so no slow password hashing)."""
    fields.setdefault('email', f'user-{uuid.uuid4().hex[:8]}@example.com')
    fields.setdefault('email_confirmed', True)
    return User.objects.create_user(**fields)


def create_vendor(
    user: User, name: str, *, vetted: bool = False, aliases: tuple[str, ...] = ()
) -> Vendor:
    """A vendor of `user` with its own name as an alias, plus an alias for each name in
    `aliases` (the names it is also known by)."""
    vendor = Vendor.objects.create(
        user=user, name=name, vetted_at=timezone.now() if vetted else None
    )
    source = VendorAliasSource.USER if vetted else VendorAliasSource.EXTRACTED
    for alias in (name, *aliases):
        VendorAlias.objects.create(
            user=user, vendor=vendor, key=normalize_key(alias), name=alias, source=source
        )
    return vendor
