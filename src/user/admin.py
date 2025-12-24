from django.contrib import admin

from .models import (
    Permission,
    Role,
    RolePermission,
    Service,
    User,
    UserGlobalPermission,
    UserGlobalRole,
    UserServiceAssignment,
    UserServicePermission,
    UserServiceRole,
)

admin.site.register(Service)
admin.site.register(Permission)
admin.site.register(Role)
admin.site.register(RolePermission)
admin.site.register(User)
admin.site.register(UserServiceAssignment)
admin.site.register(UserServiceRole)
admin.site.register(UserServicePermission)
admin.site.register(UserGlobalRole)
admin.site.register(UserGlobalPermission)
