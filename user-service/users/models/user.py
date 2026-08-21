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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # These fields are re-typed with `cast(...)` to their actual Python value type rather than
    # left as the declared `models.Field` subclass: Django model fields are descriptors handled by
    # `ModelBase`'s metaclass at runtime, but `ty` (unlike mypy+django-stubs, which has a plugin
    # for this) has no insight into that, so it type-checks instance attribute access against the
    # field class itself. `cast` overrides that with the type Django actually produces, without
    # changing what's constructed at class-body evaluation time. Only fields that are
    # read/assigned in a type-sensitive way elsewhere in the codebase are cast -- see #45. That
    # set is demand-driven, not exhaustive: it'll grow as more fields get used that way, and it
    # deliberately differs from `qr_code/qr_code/models/user.py`'s cast set, since the two models'
    # fields are read/assigned in different places.
    email = cast(str, models.EmailField(unique=True))
    name = cast(str, models.CharField(max_length=255, blank=True))
    email_confirmed = cast(
        bool, models.BooleanField(default=False, help_text='Whether email is confirmed')
    )
    email_confirmed_at = cast(
        datetime | None,
        models.DateTimeField(null=True, blank=True, help_text='When email was confirmed'),
    )
    credits = models.IntegerField(default=0, help_text='Current credits balance.')

    status = cast(
        str,
        models.CharField(
            max_length=16,
            choices=STATUS_CHOICES,
            default=STATUS_ACTIVE,
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

    def __str__(self) -> str:
        return self.email
