"""More fakes for the e2e server (`receipts_e2e_settings.py`): a file transfer bucket a browser can
really upload to and download from, and a mailbox for the emails the site sends.

Both live in the server process (`runserver --noreload`, tasks inline), so a plain in-memory
store is shared by every request. Imported lazily, like `receipts_fakes`.

- **File transfer bucket.** The unit tests' `FakeS3Storage` presigns `https://fake-s3.test/...`
  URLs nobody answers, which is enough for the drop zone test but not for a real send. The
  subclass here points the part and download URLs back at this server's `/__e2e__/ft-s3/<key>`
  view, which records a PUT part through `upload_part` (checking `x-amz-checksum-sha256` like S3)
  and answers with its `ETag`, so `complete_multipart_upload` assembles the real bytes.
- **To-do bucket.** The same idea for To-do attachments and comment files: the unit tests'
  `FakeTodoStorage` with a single presigned PUT / GET pointing back at `/__e2e__/todo-s3/<key>`.
- **Mailbox.** Every email the site sends (still printed to the console as well) is kept, newest
  first, and listed at `/__e2e__/mail/` -- so a browser-driven test can open a confirmation or
  reset link without reading the server's output. `?to=<address>` filters by recipient. In
  real-services mode the same page lists the emails read back over IMAP (`real_services.py`).
- **Preflight.** `/__e2e__/preflight/` runs `real_services.run_checks` and shows the results.
"""

import base64
import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from urllib.parse import quote

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.utils.html import escape, urlize
from django.views.decorators.csrf import csrf_exempt

from apps.core.services import email as email_service
from apps.file_transfer.services import storage as file_transfer_storage
from apps.file_transfer.tests.fakes import FakeS3Storage
from apps.todo.services import storage as todo_storage
from apps.todo.tests.fakes import FakeTodoStorage


def bucket_cors(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Answer CORS like the real buckets' rules do, so a page opened on another host name of the
    same server (`<name>2.localhost`, a second browser session) can still upload and download."""

    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if request.method == 'OPTIONS':
            response = HttpResponse(status=200)
        else:
            response = view(request, *args, **kwargs)
        response['Access-Control-Allow-Origin'] = '*'
        response['Access-Control-Allow-Methods'] = 'GET, PUT'
        response['Access-Control-Allow-Headers'] = '*'
        response['Access-Control-Expose-Headers'] = 'ETag'
        return response

    return wrapper


class FileTransferE2EStorage(FakeS3Storage):
    """`FakeS3Storage` with URLs that point back at `/__e2e__/ft-s3/`."""

    def presign_part_url(self, key: str, upload_id: str, part_number: int, *args, **kwargs) -> str:
        super().presign_part_url(key, upload_id, part_number, *args, **kwargs)
        return (
            f'{settings.BASE_URL}/__e2e__/ft-s3/{key}'
            f'?uploadId={quote(upload_id)}&partNumber={part_number}'
        )

    def presigned_get_url(self, key: str, filename: str, *args, **kwargs) -> str:
        return f'{settings.BASE_URL}/__e2e__/ft-s3/{key}?filename={quote(filename)}'


@csrf_exempt
@bucket_cors
def ft_fake_bucket(request: HttpRequest, key: str) -> HttpResponse:
    fake = file_transfer_storage.get_storage()
    if request.method == 'PUT':
        declared = request.headers.get('x-amz-checksum-sha256', '')
        actual = base64.b64encode(hashlib.sha256(request.body).digest()).decode()
        if declared and declared != actual:
            return HttpResponseBadRequest('BadDigest')
        upload_id = request.GET.get('uploadId', '')
        part_number = int(request.GET.get('partNumber', '0'))
        etag = fake.upload_part(key, upload_id, part_number, request.body)  # ty: ignore[unresolved-attribute]
        response = HttpResponse(status=200)
        response['ETag'] = f'"{etag}"'
        return response
    if request.method == 'GET':
        data = fake.objects.get(key)  # ty: ignore[unresolved-attribute]
        if data is None:
            return HttpResponse('NoSuchKey', status=404)
        response = HttpResponse(data, content_type='application/octet-stream')
        filename = request.GET.get('filename', 'download')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    return HttpResponse(status=405)


class TodoE2EStorage(FakeTodoStorage):
    """`FakeTodoStorage` with URLs that point back at `/__e2e__/todo-s3/`."""

    def presigned_put_url(self, key: str, **kwargs) -> str:
        super().presigned_put_url(key, **kwargs)
        return f'{settings.BASE_URL}/__e2e__/todo-s3/{key}'

    def presigned_get_url(self, key: str, filename: str, *args, **kwargs) -> str:
        super().presigned_get_url(key, filename, *args, **kwargs)
        return f'{settings.BASE_URL}/__e2e__/todo-s3/{key}?filename={quote(filename)}'


@csrf_exempt
@bucket_cors
def todo_fake_bucket(request: HttpRequest, key: str) -> HttpResponse:
    fake = todo_storage.get_storage()
    if request.method == 'PUT':
        declared = request.headers.get('x-amz-checksum-sha256', '')
        actual = base64.b64encode(hashlib.sha256(request.body).digest()).decode()
        if declared and declared != actual:
            return HttpResponseBadRequest('BadDigest')
        fake.objects[key] = request.body  # ty: ignore[unresolved-attribute]
        return HttpResponse(status=200)
    if request.method == 'GET':
        data = fake.objects.get(key)  # ty: ignore[unresolved-attribute]
        if data is None:
            return HttpResponse('NoSuchKey', status=404)
        return HttpResponse(data, content_type='application/octet-stream')
    return HttpResponse(status=405)


@dataclass
class SentEmail:
    to: str
    subject: str
    text_body: str
    html_body: str | None
    sent_at: datetime


_mailbox: list[SentEmail] = []
_mailbox_lock = threading.Lock()


class MailboxEmailBackend(email_service.ConsoleEmailBackend):
    """Prints like the console backend and keeps a copy for `/__e2e__/mail/`."""

    def send_email(self, to, subject, text_body, html_body=None):
        super().send_email(to, subject, text_body, html_body)
        with _mailbox_lock:
            _mailbox.insert(0, SentEmail(to, subject, text_body, html_body, datetime.now()))


def install_mailbox() -> None:
    """Make the `console` email backend kind (what `.env.local` selects) the mailbox one."""
    email_service.EMAIL_BACKEND_KIND_TO_CLASS['console'] = MailboxEmailBackend


def mailbox_view(request: HttpRequest) -> HttpResponse:
    """The emails the site sent, newest first. In real-services mode they are read back over
    IMAP (`real_services.ImapMailbox`), so an email shows up only once it has been delivered."""
    # An address has no spaces: a `+` written unescaped in the query string (`?to=a+b@x`) arrives
    # as one, so read it back as the `+` it was.
    to = request.GET.get('to', '').strip().lower().replace(' ', '+')
    note = ''
    if settings.E2E_SERVICES == 'real':
        from .real_services import mailbox

        box = mailbox()
        try:
            all_emails = box.fetch()
        except Exception as exc:  # shown on the page: the agent must see why, not a 500
            return HttpResponse(
                f'<!doctype html><title>E2E mailbox</title><h1>E2E mailbox: IMAP error</h1>'
                f'<p id="error">{escape(type(exc).__name__)}: {escape(str(exc))}</p>',
                status=503,
            )
        note = (
            '<p id="source">Sent over SMTP and read back over IMAP from '
            f'{escape(box.config.address)} (run {escape(box.run)}). Delivery takes a few '
            'seconds: reload if an email is missing.</p>'
        )
    else:
        with _mailbox_lock:
            all_emails = list(_mailbox)
        note = '<p id="source">In-memory mailbox (fakes mode).</p>'
    emails = [m for m in all_emails if not to or m.to.lower() == to]
    items = []
    for index, mail in enumerate(emails):
        sender = getattr(mail, 'sender', '')
        items.append(
            f'<article class="email" data-index="{index}" '
            'style="border:1px solid #ccc;margin:12px 0;padding:12px">'
            f'<div><b>To:</b> <span class="to">{escape(mail.to)}</span></div>'
            + (
                f'<div><b>From:</b> <span class="from">{escape(sender)}</span></div>'
                if sender
                else ''
            )
            + f'<div><b>Subject:</b> <span class="subject">{escape(mail.subject)}</span></div>'
            f'<div><b>Sent:</b> {mail.sent_at:%Y-%m-%d %H:%M:%S}</div>'
            '<pre class="text-body" style="white-space:pre-wrap">'
            f'{urlize(mail.text_body, autoescape=True)}</pre>'
            + (
                '<details><summary>HTML body (source)</summary>'
                '<pre class="html-body" style="white-space:pre-wrap">'
                f'{escape(mail.html_body)}</pre></details>'
                if mail.html_body
                else ''
            )
            + '</article>'
        )
    body = ''.join(items) or '<p id="empty">No emails.</p>'
    html = (
        '<!doctype html><html><head><meta charset="utf-8"><title>E2E mailbox</title></head>'
        f'<body style="font-family:sans-serif"><h1>E2E mailbox ({len(emails)})</h1>{note}{body}'
        '</body></html>'
    )
    return HttpResponse(html)


def preflight_view(request: HttpRequest) -> HttpResponse:
    """Run the preflight checks (`real_services.run_checks`) now and show them. `?mail=0` skips
    the SMTP/IMAP round trip, which takes a few seconds."""
    from .real_services import run_checks

    real = settings.E2E_SERVICES == 'real'
    checks = run_checks(real=real, mail=request.GET.get('mail') != '0')
    ok = all(c.ok for c in checks)
    rows = ''.join(
        f'<tr class="check {"pass" if c.ok else "fail"}"><td>{escape(c.area)}</td>'
        f'<td>{escape(c.name)}</td><td><b>{"PASS" if c.ok else "FAIL"}</b></td>'
        f'<td>{escape(c.detail)}</td></tr>'
        for c in checks
    )
    mode = 'real services (Postgres, Floci, SMTP/IMAP)' if real else 'fakes (in-memory)'
    html = (
        '<!doctype html><html><head><meta charset="utf-8"><title>E2E preflight</title></head>'
        '<body style="font-family:sans-serif">'
        f'<h1>E2E preflight: <span id="result">{"PASS" if ok else "FAIL"}</span></h1>'
        f'<p id="mode">Mode: {escape(mode)}</p>'
        '<table border="1" cellpadding="6" style="border-collapse:collapse">'
        '<tr><th>Area</th><th>Check</th><th>Result</th><th>Detail</th></tr>'
        f'{rows}</table></body></html>'
    )
    return HttpResponse(html, status=200 if ok else 503)
