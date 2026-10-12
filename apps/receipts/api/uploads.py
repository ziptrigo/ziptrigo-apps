"""The direct-to-S3 upload endpoints (`/api/receipts/uploads`): the API/CLI twin of the upload
page's JSON endpoints (`apps.receipts.views.uploads`), sharing their rate limits.

1. `POST /uploads` registers the file and returns a presigned `PUT` URL.
2. The client `PUT`s the bytes to it with the returned headers.
3. `POST /uploads/{receipt_id}/complete` verifies the object and queues the extraction; the
   receipt then appears in the list and moves through `received` -> `processing` -> its result.
"""

from uuid import UUID

from ninja.errors import HttpError

from apps.core import ratelimit

from .. import services
from ..models import ReceiptSource
from ..schemas import ReceiptSchema, UploadStartSchema, UploadTicketSchema
from .common import mapped_errors, owned_receipt
from .router import router


@router.post('/uploads', response={201: UploadTicketSchema})
def start_upload(request, payload: UploadStartSchema):
    """Start uploading one file (a PDF, JPG, PNG, HEIC or WebP). 402 while the account is frozen;
    400 for an unsupported or oversized file, a malformed checksum or the daily file cap."""
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'RECEIPTS_UPLOAD_USER'))
    if not payload.filename.strip():
        raise HttpError(400, 'The file needs a name.')
    with mapped_errors():
        ticket = services.start_upload(
            request.auth,
            payload.filename,
            payload.size,
            payload.content_type,
            payload.checksum_sha256,
            source=ReceiptSource.API,
        )
    return 201, {
        'receipt_id': ticket.receipt.pk,
        'url': ticket.url,
        'headers': ticket.headers,
        'expires_in': ticket.expires_in,
    }


@router.post('/uploads/{receipt_id}/complete', response=ReceiptSchema)
def complete_upload(request, receipt_id: UUID):
    """Finish an upload after the `PUT`. Idempotent. 400 if the object isn't there yet, or was
    rejected (wrong size, corrupted, or contents that aren't an allowed type: the receipt then
    stays in the list as `failed`)."""
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'RECEIPTS_UPLOAD_COMPLETE_USER'))
    receipt = owned_receipt(request, receipt_id, include_unfinished=True)
    with mapped_errors():
        return services.complete_upload(receipt)
