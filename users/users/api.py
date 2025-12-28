from ninja import NinjaAPI

from .routers import auth, roles_permissions, services, users

api = NinjaAPI(
    title='User Service API',
    version='1.0.0',
    description='Centralized SSO service providing JWT auth, services, users, roles, and permissions.',
)

# Register routers
api.add_router('/auth/', auth.router, tags=['Authentication'])
api.add_router('/services/', services.router, tags=['Services'])
api.add_router('/services/', roles_permissions.router, tags=['Roles & Permissions'])
api.add_router('/users/', users.router, tags=['Users'])
api.add_router('/services/', users.service_users_router, tags=['Users'])
