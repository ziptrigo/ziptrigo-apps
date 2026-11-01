import uuid
from datetime import datetime
from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models


class KnownEmail(models.Model):
    """An address that has been confirmed on some account, ever (issue #160): the memory behind
    "a genuinely new account". Never deleted with the user, so closing an account, or moving an
    account to another address, does not make the old one new again.

    It holds a keyed hash, not the address (`services.known_emails.fingerprint`: HMAC-SHA256 of the
    *canonical* form, with `SECRET_KEY`), so keeping it after an account is deleted keeps no
    address. `first_user_id` is a plain UUID, not a foreign key, for the same reason: the row must
    survive the user. The first account to confirm the address is its holder; anyone after that
    (another account, a `+tag` variant, a dotted Gmail variant) is not.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    fingerprint = cast(str, models.CharField(max_length=64, primary_key=True))
    first_user_id = cast(uuid.UUID, models.UUIDField())
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        verbose_name = 'Known email'
        verbose_name_plural = 'Known emails'

    def __str__(self) -> str:
        return self.fingerprint[:12]
