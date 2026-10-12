"""A user's Receipts account: creating it (with its categories and first inbox code), the inbox
address, and the stored-bytes counter that storage billing reads."""

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from apps.accounts.models import User

from ..models import DEFAULT_CATEGORIES, InboxCode, ReceiptCategory, ReceiptsAccount


def get_account(user: User) -> ReceiptsAccount:
    """The user's `ReceiptsAccount`, created on first use.

    Creating one seeds the default categories and issues the first inbox code, all in one
    transaction. The billing clock starts at creation (`last_billed_at`), and again whenever the
    account goes from empty to holding bytes (`add_stored_bytes`), so nobody is billed for time
    before anything was stored.
    """
    account = ReceiptsAccount.objects.filter(user=user).first()
    if account is not None:
        return account
    try:
        with transaction.atomic():
            account = ReceiptsAccount.objects.create(user=user, last_billed_at=timezone.now())
            ReceiptCategory.objects.bulk_create(
                ReceiptCategory(user=user, name=name, sort_order=index)
                for index, name in enumerate(DEFAULT_CATEGORIES)
            )
            InboxCode.issue(account)
    except IntegrityError:
        # A concurrent first visit created it.
        return ReceiptsAccount.objects.get(user=user)
    return account


def active_inbox_code(account: ReceiptsAccount) -> InboxCode:
    """The account's active code (issuing one if it somehow has none)."""
    code = InboxCode.objects.filter(account=account, active=True).first()
    return code if code is not None else InboxCode.issue(account)


def inbox_address(code: str) -> str:
    """`receipts+<code>@<domain>`, from `settings.RECEIPTS_INBOUND_ADDRESS` (`receipts@ziptrigo.com`)."""
    local, _, domain = settings.RECEIPTS_INBOUND_ADDRESS.partition('@')
    local = local.split('+', 1)[0]
    return f'{local}+{code}@{domain}'


def inbox_address_for(user: User) -> str:
    return inbox_address(active_inbox_code(get_account(user)).code)


def is_frozen(user: User) -> bool:
    return ReceiptsAccount.objects.filter(user=user, frozen_at__isnull=False).exists()


def add_stored_bytes(user_id: object, delta: int) -> None:
    """Add `delta` (negative to remove) to the account's `stored_bytes`, never going below zero.

    Every object written or deleted for an account goes through here (files, previews, stripped
    emails), so billing sees exactly what is stored. When an empty account gets its first bytes the
    billing clock restarts, so the empty stretch before is never billed.
    """
    if not delta:
        return
    if delta > 0:
        ReceiptsAccount.objects.filter(pk=user_id, stored_bytes=0).update(
            last_billed_at=timezone.now()
        )
    ReceiptsAccount.objects.filter(pk=user_id).update(
        stored_bytes=Greatest(F('stored_bytes') + delta, Value(0))
    )
