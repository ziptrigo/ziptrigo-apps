from typing import Any

from ninja_jwt.tokens import Token

from .models import (
    RolePermission,
    User,
    UserGlobalPermission,
    UserGlobalRole,
    UserServiceAssignment,
    UserServicePermission,
    UserServiceRole,
)


class CustomAccessToken(Token):
    """Custom access token that includes permission and role claims."""

    token_type = 'access'
    lifetime_setting = 'ACCESS_TOKEN_LIFETIME'

    @classmethod
    def for_user(cls, user: User) -> 'CustomAccessToken':  # type: ignore[override]
        """
        Create a token for the given user with custom claims.

        Includes global_permissions, global_roles, and services data.
        """
        token = super().for_user(user)

        # Add email to token
        token['email'] = user.email

        # Collect global permissions
        global_perms: set[str] = set()
        direct_global_perms = UserGlobalPermission.objects.filter(user=user).select_related(
            'permission'
        )
        for ugp in direct_global_perms:
            global_perms.add(ugp.permission.code)

        # Collect global roles and their permissions
        global_roles: list[str] = []
        user_global_roles = UserGlobalRole.objects.filter(user=user).select_related('role')
        for ugr in user_global_roles:
            global_roles.append(ugr.role.name)
            role_perms = RolePermission.objects.filter(role=ugr.role).select_related('permission')
            for rp in role_perms:
                global_perms.add(rp.permission.code)

        # Collect service-specific data
        services_data: dict[str, dict[str, Any]] = {}
        assignments = UserServiceAssignment.objects.filter(user=user).select_related('service')

        for assignment in assignments:
            service_id = str(assignment.service.id)
            service_perms: set[str] = set()
            service_roles: list[str] = []

            # Direct service permissions
            direct_perms = UserServicePermission.objects.filter(
                user=user, service=assignment.service
            ).select_related('permission')
            for usp in direct_perms:
                service_perms.add(usp.permission.code)

            # Service roles and their permissions
            user_roles = UserServiceRole.objects.filter(
                user=user, service=assignment.service
            ).select_related('role')
            for usr in user_roles:
                service_roles.append(usr.role.name)
                role_perms = RolePermission.objects.filter(role=usr.role).select_related(
                    'permission'
                )
                for rp in role_perms:
                    service_perms.add(rp.permission.code)

            services_data[service_id] = {
                'permissions': sorted(list(service_perms)),
                'roles': service_roles,
            }

        # Add custom claims to token
        token['global_permissions'] = sorted(list(global_perms))
        token['global_roles'] = global_roles
        token['services'] = services_data

        return token  # type: ignore


class CustomRefreshToken(Token):
    """Custom refresh token."""

    token_type = 'refresh'
    lifetime_setting = 'REFRESH_TOKEN_LIFETIME'
    no_copy_claims = (
        'token_type',
        'exp',
        'iat',
        'jti',
    )

    @property
    def access_token(self) -> CustomAccessToken:
        """Get an access token from this refresh token."""
        access = CustomAccessToken()

        # Copy claims from refresh token (except no_copy_claims)
        for claim, value in self.payload.items():
            if claim in self.no_copy_claims:
                continue
            access[claim] = value

        return access

    @classmethod
    def for_user(cls, user: User) -> 'CustomRefreshToken':  # type: ignore[override]
        """Create a refresh token for the given user."""
        token = super().for_user(user)
        token['email'] = user.email

        return token  # type: ignore
