"""Main Django Ninja API router configuration."""

from ninja_extra import NinjaExtraAPI
from ninja_jwt.controller import NinjaJWTDefaultController

from .auth_new import router as auth_router
from .qrcode_new import router as qrcode_router
from .redirect import router as redirect_router

# Create the main API instance
api = NinjaExtraAPI(
    title='QR Code API',
    version='2.0.0',
    description='QR Code generation and management API with JWT authentication',
)

# Register JWT authentication controllers (token/pair, token/refresh, token/verify)
api.register_controllers(NinjaJWTDefaultController)

api.add_router('/auth/', auth_router, tags=['Authentication'])
api.add_router('/qrcodes/', qrcode_router, tags=['QR Codes'])
api.add_router('/go/', redirect_router, tags=['Redirect'])
