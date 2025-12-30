from ninja import NinjaAPI

from .routers import account, auth, credits, users

api = NinjaAPI(
    title='User Service API',
    version='1.0.0',
    description='Centralized SSO service providing JWT auth and user management.',
)

# Register routers
api.add_router('/', account.router, tags=['Account'])
api.add_router('/auth/', auth.router, tags=['Authentication'])
api.add_router('/users/', users.router, tags=['Users'])
api.add_router('/users/', credits.router, tags=['Credits'])
