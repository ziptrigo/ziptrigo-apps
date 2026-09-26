"""The site's single Django Ninja API. Each app contributes a router under its own prefix."""

from ninja import NinjaAPI

from apps.accounts.api import router as accounts_router
from apps.billing.api import router as billing_router
from apps.qr_code.api import router as qr_code_router

api = NinjaAPI(
    title='ZipTrigo API',
    version='1.0.0',
    description='API for every ZipTrigo app. Authenticate with a JWT from `/api/auth/login`.',
)

api.add_router('/', accounts_router)
api.add_router('/billing/', billing_router)
api.add_router('/qr/', qr_code_router)
