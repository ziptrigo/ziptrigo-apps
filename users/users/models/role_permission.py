from django.db import models


class RolePermission(models.Model):
    role = models.ForeignKey(
        'Role',
        on_delete=models.CASCADE,
        related_name='role_permissions',
    )
    permission = models.ForeignKey(
        'Permission',
        on_delete=models.CASCADE,
        related_name='permission_roles',
    )

    class Meta:
        unique_together = ('role', 'permission')
