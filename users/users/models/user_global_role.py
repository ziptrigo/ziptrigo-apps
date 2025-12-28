from django.db import models


class UserGlobalRole(models.Model):
    user = models.ForeignKey(
        'User',
        on_delete=models.CASCADE,
        related_name='global_roles',
    )
    role = models.ForeignKey(
        'Role',
        on_delete=models.CASCADE,
    )

    class Meta:
        unique_together = ('user', 'role')
