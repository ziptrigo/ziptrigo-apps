from django.db import models


class UserGlobalPermission(models.Model):
    user = models.ForeignKey(
        'User',
        on_delete=models.CASCADE,
        related_name='global_permissions',
    )
    permission = models.ForeignKey(
        'Permission',
        on_delete=models.CASCADE,
    )

    class Meta:
        unique_together = ('user', 'permission')
