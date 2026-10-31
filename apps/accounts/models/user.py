import uuid
from datetime import datetime
from typing import ClassVar, cast

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils import timezone

from ..validators import validate_timezone


class UserManager(BaseUserManager):
    def create_user(
        self,
        email: str,
        password: str | None = None,
        **extra_fields,
    ) -> 'User':
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(
        self,
        email: str,
        password: str | None = None,
        **extra_fields,
    ) -> 'User':
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    STATUS_ACTIVE = 'ACTIVE'
    STATUS_INACTIVE = 'INACTIVE'
    STATUS_DELETED = 'DELETED'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_INACTIVE, 'Inactive'),
        (STATUS_DELETED, 'Deleted'),
    ]

    THEME_SYSTEM = 'system'
    THEME_LIGHT = 'light'
    THEME_DARK = 'dark'
    THEME_CHOICES = [
        (THEME_SYSTEM, 'System'),
        (THEME_LIGHT, 'Light'),
        (THEME_DARK, 'Dark'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # These fields are re-typed with `cast(...)` to their actual Python value type rather than
    # left as the declared `models.Field` subclass: Django model fields are descriptors handled by
    # `ModelBase`'s metaclass at runtime, but `ty` (unlike mypy+django-stubs, which has a plugin
    # for this) has no insight into that, so it type-checks instance attribute access against the
    # field class itself. `cast` overrides that with the type Django actually produces, without
    # changing what's constructed at class-body evaluation time. Only fields that are
    # read/assigned in a type-sensitive way elsewhere in the codebase are cast -- see #45. That
    # set is demand-driven, not exhaustive: it'll grow as more fields get used that way, and it
    # reflects where this model's fields happen to be read/assigned today.
    email = cast(str, models.EmailField(unique=True))
    name = cast(str, models.CharField(max_length=255, blank=True))
    email_confirmed = cast(
        bool, models.BooleanField(default=False, help_text='Whether email is confirmed')
    )
    email_confirmed_at = cast(
        datetime | None,
        models.DateTimeField(null=True, blank=True, help_text='When email was confirmed'),
    )
    # Google's stable account identifier (the OpenID Connect `sub` claim), set the first time the
    # user signs in with Google (`apps.accounts.services.google_oauth`). Matched before the email,
    # since a Google account's address can change while its `sub` never does. Null for accounts
    # that never used Google; unique so one Google account can't be linked to two users.
    google_sub = cast(
        str | None,
        models.CharField(max_length=255, null=True, blank=True, unique=True),
    )
    # Microsoft's stable identifier for the user, `<tid>:<oid>` (the ID token's tenant and object
    # id, lower-cased), set the first time the user signs in with Microsoft
    # (`apps.accounts.services.microsoft_oauth`). Matched before the email, which in a work/school
    # tenant is mutable and not proof of ownership. Not the `sub` claim: that is unique per user
    # *and per app registration*, so recreating the registration would orphan every link, whereas
    # `oid` is the same for every app. Null for accounts that never used Microsoft; unique so one
    # Microsoft account can't be linked to two users.
    microsoft_id = cast(
        str | None,
        models.CharField(max_length=255, null=True, blank=True, unique=True),
    )
    status = cast(
        str,
        models.CharField(
            max_length=16,
            choices=STATUS_CHOICES,
            default=STATUS_ACTIVE,
        ),
    )
    # The colour theme the site shows this user (issue #166), set on the account page and rendered
    # by `core/base.html` on every page. `db_default` as well as `default`: the previous release's
    # container keeps inserting users while the migration runs and doesn't know the column.
    theme = cast(
        str,
        models.CharField(
            max_length=16,
            choices=THEME_CHOICES,
            default=THEME_SYSTEM,
            db_default=THEME_SYSTEM,
        ),
    )
    # The user's IANA time zone (issue #177), for "today", due dates with times and reminders in
    # their local time. `''` means not set yet and is treated as UTC everywhere. Set on the account
    # page or detected from the browser (`apps.accounts.services.timezones.set_timezone`, which
    # also sends `timezone_changed`). `db_default` as well as `default`, as for `theme`: the
    # previous release's container keeps inserting users while the migration runs.
    timezone = cast(
        str,
        models.CharField(
            max_length=64,
            blank=True,
            default='',
            db_default='',
            validators=[validate_timezone],
        ),
    )
    inactive_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    inactive_reason = cast(str, models.TextField(blank=True))
    deleted_at = models.DateTimeField(null=True, blank=True)

    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()
    # Django's `ModelBase` metaclass injects `DoesNotExist` on every concrete model; `ty` has no
    # equivalent of django-stubs' plugin for that, so it doesn't know this attribute exists. See
    # the field-casting comment above for the same root cause.
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    def mark_deleted(self) -> None:
        self.status = self.STATUS_DELETED
        self.deleted_at = timezone.now()
        self.save(update_fields=['status', 'deleted_at'])

    def deactivate(self, reason: str = '') -> None:
        self.status = self.STATUS_INACTIVE
        self.inactive_at = timezone.now()
        self.inactive_reason = reason
        self.save(update_fields=['status', 'inactive_at', 'inactive_reason'])

    def reactivate(self) -> None:
        self.status = self.STATUS_ACTIVE
        self.inactive_at = None
        self.inactive_reason = ''
        self.save(update_fields=['status', 'inactive_at', 'inactive_reason'])

    @property
    def social_sign_in_providers(self) -> str:
        """The provider(s) an account without a password signs in with, named for the note the
        account page shows instead of the change-password form: `Google`, `Microsoft`, or `Google
        and Microsoft` when both are linked. An account linked to neither (an unusable password
        set some other way) is shown as `Google`, the first one offered. One place, so every view
        that renders the page agrees."""
        providers = []
        if self.google_sub:
            providers.append('Google')
        if self.microsoft_id:
            providers.append('Microsoft')
        return ' and '.join(providers) or 'Google'

    def __str__(self) -> str:
        return self.email
