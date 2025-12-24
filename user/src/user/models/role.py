import uuid

from django.db import models


class Role(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(
        'Service',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='roles',
    )
    name = models.CharField(max_length=64)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['service', 'name'],
                name='unique_role_name_per_service',
            )
        ]

    def __str__(self) -> str:
        return self.name
