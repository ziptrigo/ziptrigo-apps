"""The site's single Django Ninja API. Each app contributes a router under its own prefix."""

from ninja import NinjaAPI

from apps.accounts.api import router as accounts_router
from apps.billing.api import router as billing_router
from apps.core.ratelimit import RateLimitExceeded
from apps.file_transfer.api import router as file_transfer_router
from apps.qr_code.api import router as qr_code_router
from apps.receipts.api import router as receipts_router
from apps.short_url.api import router as short_url_router
from apps.todo.api import router as todo_router

api = NinjaAPI(
    title='ZipTrigo API',
    version='1.0.0',
    description='API for every ZipTrigo app. Authenticate with a JWT from `/api/auth/login`.',
)

api.add_router('/', accounts_router)
api.add_router('/billing/', billing_router)
api.add_router('/qr/', qr_code_router)
api.add_router('/ft/', file_transfer_router)
api.add_router('/receipts/', receipts_router)
api.add_router('/s/', short_url_router)
api.add_router('/todo/', todo_router)


@api.exception_handler(RateLimitExceeded)
def handle_rate_limit_exceeded(request, exc: RateLimitExceeded):
    """Every router's `ratelimit.enforce(...)` raises this on a 429; one handler for the whole
    API means a `Retry-After` header (which ninja's own `HttpError` path has no hook for) is set
    consistently everywhere, rather than each endpoint building its own response (issue #53)."""
    response = api.create_response(request, {'detail': exc.message}, status=429)
    response['Retry-After'] = str(exc.retry_after)
    return response
