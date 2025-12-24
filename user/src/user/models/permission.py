import uuid

from django.db import models
from django.db.models import Q


class Permission(models.Model):
    TYPE_GLOBAL = 'GLOBAL'
    TYPE_SERVICE = 'SERVICE'
    TYPE_CHOICES = [
        (TYPE_GLOBAL, 'Global'),
        (TYPE_SERVICE, 'Service'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    service = models.ForeignKey(
        'Service',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='permissions',
    )
    code = models.CharField(max_length=64)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['type', 'code'],
                condition=Q(type='GLOBAL'),
                name='unique_global_permission_code',
            ),
            models.UniqueConstraint(
                fields=['type', 'service', 'code'],
                condition=Q(type='SERVICE'),
                name='unique_service_permission_code',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.type}:{self.code}'
