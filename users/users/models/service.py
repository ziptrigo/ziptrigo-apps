import uuid

from django.db import models


class Service(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    client_id = models.CharField(max_length=64, unique=True)
    client_secret = models.CharField(max_length=128)
    status = models.CharField(
        max_length=32,
        choices=[('ACTIVE', 'Active'), ('INACTIVE', 'Inactive')],
        default='ACTIVE',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name
