"""The in-memory fakes of the Receipts e2e server and the view serving its fake bucket.

Imported lazily (`receipts_e2e.E2EConfig.ready`, the URLconf): it touches models, which are not
loaded yet when `INSTALLED_APPS` is being read."""

import base64
import hashlib

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt

from apps.receipts.services import documents, storage
from apps.receipts.tests.fakes import FakeReceiptsStorage


class E2EStorage(FakeReceiptsStorage):
    """The in-memory bucket, with URLs that point back at this server's `/__e2e__/s3/` view."""

    def _url(self, key: str, query: str = '') -> str:
        return f'{settings.BASE_URL}/__e2e__/s3/{key}{query}'

    def presigned_put_url(self, key: str, **kwargs) -> str:
        super().presigned_put_url(key, **kwargs)
        return self._url(key)

    def presigned_get_url(self, key: str, filename: str, *args, **kwargs) -> str:
        super().presigned_get_url(key, filename, *args, **kwargs)
        return self._url(key, f'?disposition={kwargs.get("disposition", "attachment")}')


@csrf_exempt
def fake_bucket(request: HttpRequest, key: str) -> HttpResponse:
    """PUT stores the body under `key` after checking the checksum header the way S3 would; GET
    returns it (`disposition=attachment` downloads, anything else shows inline)."""
    fake = storage.get_storage()
    if request.method == 'PUT':
        declared = request.headers.get('x-amz-checksum-sha256', '')
        actual = base64.b64encode(hashlib.sha256(request.body).digest()).decode()
        if declared != actual:
            return HttpResponseBadRequest('BadDigest')
        fake.seed(key, request.body)  # ty: ignore[unresolved-attribute]
        return HttpResponse(status=200)
    if request.method == 'GET':
        data = fake.objects.get(key)  # ty: ignore[unresolved-attribute]
        if data is None:
            return HttpResponse(status=404)
        content_type = (
            documents.sniff_content_type(data, allow_text=True) or 'application/octet-stream'
        )
        response = HttpResponse(data, content_type=content_type)
        if request.GET.get('disposition') == 'attachment':
            response['Content-Disposition'] = 'attachment'
        return response
    return HttpResponse(status=405)
