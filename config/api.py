"""The site's single Django Ninja API. Each app contributes a router under its own prefix."""

from ninja import NinjaAPI

from apps.accounts.api import router as accounts_router
from apps.billing.api import router as billing_router
from apps.core.ratelimit import RateLimitExceeded
from apps.file_transfer.api import router as file_transfer_router
from apps.notes.api import router as notes_router
from apps.qr_code.api import router as qr_code_router
from apps.receipts.api import router as receipts_router
from apps.short_url.api import router as short_url_router
from apps.todo.api import router as todo_router
from config.api_version import API_VERSION

# The spec's top-level `tags`: the order Swagger UI and generated clients group the endpoints in,
# and a line on each. Every tag a router sets must be here (`apps/core/tests/test_openapi_doc.py`).
TAGS = [
    {
        'name': 'Authentication',
        'description': 'Sign in for a JWT, refresh it, and the password reset and email '
        'confirmation flows. Accounts are created only on the website.',
    },
    {
        'name': 'Account',
        'description': 'The signed-in user: name, email, time zone, date and time format.',
    },
    {'name': 'Credits', 'description': "Staff only: a user's credit balance and adjustments."},
    {'name': 'QR Codes', 'description': 'QR codes, tracked or not: create, edit, preview, images.'},
    {
        'name': 'File Transfer',
        'description': 'Send files: drafts, resumable uploads to S3, recipients, the transfers '
        'you sent.',
    },
    {
        'name': 'Receipts',
        'description': 'Receipts and invoices: uploads, the fields read from them, vendors, '
        'categories, the CSV export and the inbox address.',
    },
    {
        'name': 'Short URLs',
        'description': 'Short links with editable destinations, their paid extras and click '
        'statistics.',
    },
    {
        'name': 'To-do',
        'description': 'Tasks and subtasks with states (to do, in progress, on hold, blocked), '
        "resolutions (done, won't do) and blockers, labels, comments, reminders, attachments, "
        "saved filters, settings and each task's activity log. A task is addressed by its number, "
        'everything else by its id. Errors are `{"detail": ...}`: 400 a value refused (a service '
        'message, or a bad query parameter), 402 not enough credits or storage unpaid, 404 not '
        'yours or not there (never 403), 409 a conflict (creation blocked by an unpaid repeating '
        'task: also `unpaid_task_numbers`), 422 a malformed request, 429 rate limited (see '
        '`Retry-After`).',
    },
    {
        'name': 'Notes',
        'description': 'Markdown notes, their versions, tags, share links and saved filters.',
    },
]

api = NinjaAPI(
    title='ZipTrigo API',
    version=API_VERSION,
    description='API for every ZipTrigo app. Authenticate with a JWT from `/api/auth/login`.',
    openapi_extra={'tags': TAGS},
)

api.add_router('/', accounts_router)
api.add_router('/billing/', billing_router)
api.add_router('/qr/', qr_code_router)
api.add_router('/ft/', file_transfer_router)
api.add_router('/receipts/', receipts_router)
api.add_router('/s/', short_url_router)
api.add_router('/todo/', todo_router)
api.add_router('/notes/', notes_router)


@api.exception_handler(RateLimitExceeded)
def handle_rate_limit_exceeded(request, exc: RateLimitExceeded):
    """Every router's `ratelimit.enforce(...)` raises this on a 429; one handler for the whole
    API means a `Retry-After` header (which ninja's own `HttpError` path has no hook for) is set
    consistently everywhere, rather than each endpoint building its own response (issue #53)."""
    response = api.create_response(request, {'detail': exc.message}, status=429)
    response['Retry-After'] = str(exc.retry_after)
    return response
