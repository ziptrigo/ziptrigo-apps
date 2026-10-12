from datetime import datetime
from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, models, transaction
from django.utils.crypto import get_random_string

from .account import ReceiptsAccount

CODE_LENGTH = 8

#: Lowercase letters and digits minus the look-alikes (`i`, `l`, `o`, `0`, `1`), so a code read
#: aloud or off a screenshot is hard to get wrong. 31 symbols ^ 8 is about 8.5e11 codes.
CODE_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'

_MAX_COLLISION_RETRIES = 20


def generate_code() -> str:
    """A fresh random code (not checked for uniqueness; `InboxCode.issue` does that)."""
    return get_random_string(CODE_LENGTH, allowed_chars=CODE_ALPHABET)


class InboxCode(models.Model):
    """One `receipts+<code>@...` address of an account. Exactly one is active at a time;
    regenerating retires the old one immediately. Retired codes are kept forever and never
    reissued (`code` is unique across all rows), so an old forwarding rule can never deliver to
    someone else."""

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    account = models.ForeignKey(ReceiptsAccount, on_delete=models.CASCADE, related_name='codes')
    code = cast(str, models.CharField(max_length=CODE_LENGTH, unique=True))
    active = cast(bool, models.BooleanField(default=True))
    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))
    retired_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['account'],
                condition=models.Q(active=True),
                name='receipts_one_active_inbox_code_per_account',
            )
        ]
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.code} ({"active" if self.active else "retired"})'

    @classmethod
    def issue(cls, account: ReceiptsAccount) -> 'InboxCode':
        """Create a new active code for `account` with a code no row (active or retired) has ever
        had, retrying on the (unlikely) collision."""
        for _ in range(_MAX_COLLISION_RETRIES):
            code = generate_code()
            if cls.objects.filter(code=code).exists():
                continue
            try:
                # A savepoint, so losing a race on the unique column doesn't poison a caller's
                # transaction.
                with transaction.atomic():
                    return cls.objects.create(account=account, code=code)
            except IntegrityError:
                if cls.objects.filter(account=account, active=True).exists():
                    raise
                continue
        raise RuntimeError('Could not generate a unique inbox code')  # pragma: no cover
