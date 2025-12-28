import uuid

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
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
            user.set_password(password)  # type: ignore
        else:
            user.set_unusable_password()  # type: ignore
        user.save(using=self._db)
        return user  # type: ignore

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
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255, blank=True)

    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
    )
    inactive_at = models.DateTimeField(null=True, blank=True)
    inactive_reason = models.TextField(blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

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
