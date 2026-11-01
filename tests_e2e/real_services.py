"""The real-services mode of the AI-agent test server (`tests_e2e/e2e_env.py` describes the modes):
email over SMTP read back over IMAP, the Floci bucket, and the preflight checks.

Imported only by the e2e server (`receipts_e2e.E2EConfig.ready`, its URLconf) and
`tests_e2e/ai/preflight.py`, never by the site itself.

**Email.** `SmtpEmailBackend` sends every email the site sends through the SMTP server of
`emails.yaml`, as a real multipart message with the site's own subject and bodies and the
recipient the site chose in `To:`. The *envelope* recipient, though, is always the one mailbox of
`emails.yaml` (`imap.address`): the plans use made-up addresses (`qa.user@example.com`), and a
catch-all mailbox can't be assumed (plus-addressing wouldn't do either: welcome credits treat
`a+tag@` as `a@`). Two headers tie a message to the run: `X-E2E-Run` (the server's run id) and
`X-E2E-Original-To`. `ImapMailbox` reads them back, so `/__e2e__/mail/?to=<address>` lists what
the site sent to that address in this run, as delivered.

**Bucket.** One Floci bucket per server port (`ziptrigo-e2e-ai[-<port>]`) holds every product's
keys (`transfers/`, `receipts/`, `todo/`), like the deployments' single app bucket. `prepare_bucket`
creates it, empties it for a fresh run and sets the CORS rule the browser uploads need.
"""

import base64
import email
import email.policy
import hashlib
import imaplib
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parsedate_to_datetime

from django.conf import settings

from tests_e2e import e2e_env

RUN_HEADER = 'X-E2E-Run'
ORIGINAL_TO_HEADER = 'X-E2E-Original-To'


def run_id() -> str:
    return settings.E2E_RUN_ID


# --- Email -----------------------------------------------------------------------------------


def smtp_connect(config: e2e_env.SmtpConfig) -> smtplib.SMTP:
    context = ssl.create_default_context()
    if config.security == 'ssl':
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            config.host, config.port, timeout=30, context=context
        )
    else:
        client = smtplib.SMTP(config.host, config.port, timeout=30)
        if config.security == 'starttls':
            client.starttls(context=context)
    client.login(config.username, config.password)
    return client


def build_message(
    *, to: str, subject: str, text_body: str, html_body: str | None, sender: str, run: str
) -> EmailMessage:
    message = EmailMessage()
    message['From'] = sender
    message['To'] = to
    message['Subject'] = subject
    message['Date'] = formatdate(localtime=True)
    message['Message-ID'] = make_msgid(domain='e2e.ziptrigo.localhost')
    message[RUN_HEADER] = run
    message[ORIGINAL_TO_HEADER] = to
    message.set_content(text_body)
    if html_body is not None:
        message.add_alternative(html_body, subtype='html')
    return message


class SmtpEmailBackend:
    """The site's email backend in real-services mode (installed in place of every kind)."""

    def send_email(self, to: str, subject: str, text_body: str, html_body: str | None = None):
        config = e2e_env.load_mail_config()
        message = build_message(
            to=to,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            sender=config.smtp.sender,
            run=run_id(),
        )
        try:
            with smtp_connect(config.smtp) as client:
                client.send_message(
                    message, from_addr=config.smtp.sender, to_addrs=[config.imap.address]
                )
        except (OSError, smtplib.SMTPException) as exc:
            raise RuntimeError(
                f'Error sending email via SMTP: {type(exc).__name__}: {exc}'
            ) from exc


def install_smtp_backend() -> None:
    """Every email backend kind the env file might select (`console`, `ses`) sends over SMTP."""
    from apps.core.services import email as email_service

    for kind in list(email_service.EMAIL_BACKEND_KIND_TO_CLASS):
        email_service.EMAIL_BACKEND_KIND_TO_CLASS[kind] = SmtpEmailBackend


@dataclass
class ReceivedEmail:
    to: str
    subject: str
    text_body: str
    html_body: str | None
    sent_at: datetime
    sender: str = ''
    folder: str = ''


def imap_connect(config: e2e_env.ImapConfig) -> imaplib.IMAP4:
    context = ssl.create_default_context()
    if config.security == 'ssl':
        client: imaplib.IMAP4 = imaplib.IMAP4_SSL(
            config.host, config.port, ssl_context=context, timeout=30
        )
    else:
        client = imaplib.IMAP4(config.host, config.port, timeout=30)
        if config.security == 'starttls':
            client.starttls(ssl_context=context)
    client.login(config.username, config.password)
    return client


def _quote_folder(folder: str) -> str:
    return '"' + folder.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _parse(raw: bytes, folder: str) -> ReceivedEmail:
    message = email.message_from_bytes(raw, policy=email.policy.default)
    text = message.get_body(preferencelist=('plain',))
    html = message.get_body(preferencelist=('html',))
    try:
        sent_at = parsedate_to_datetime(str(message['Date']))
    except TypeError, ValueError:
        sent_at = datetime.now(UTC)
    return ReceivedEmail(
        to=str(message[ORIGINAL_TO_HEADER] or message['To'] or ''),
        subject=str(message['Subject'] or ''),
        text_body=text.get_content() if text is not None else '',
        html_body=html.get_content() if html is not None else None,
        sent_at=sent_at,
        sender=str(message['From'] or ''),
        folder=folder,
    )


@dataclass
class ImapMailbox:
    """The emails of one run, read from the IMAP mailbox of `emails.yaml`. Messages already read
    are kept in memory (keyed by folder, UIDVALIDITY and UID), so a reload only fetches new ones."""

    config: e2e_env.ImapConfig
    run: str
    _cache: dict[tuple[str, str, str], ReceivedEmail | None] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def fetch(self, since_days: int = 2) -> list[ReceivedEmail]:
        """Every message of this run, newest first."""
        since = (datetime.now(UTC) - timedelta(days=since_days)).strftime('%d-%b-%Y')
        found: list[ReceivedEmail] = []
        with self._lock, imap_connect(self.config) as client:
            for folder in self.config.folders:
                status, data = client.select(_quote_folder(folder), readonly=True)
                if status != 'OK':
                    continue
                validity = client.response('UIDVALIDITY')[1]
                key_validity = (validity[0] or b'').decode() if validity else ''
                status, data = client.uid('SEARCH', 'SINCE', since)
                if status != 'OK' or not data or not data[0]:
                    continue
                uids = data[0].decode().split()
                unknown = [u for u in uids if (folder, key_validity, u) not in self._cache]
                for uid in unknown:
                    self._cache[(folder, key_validity, uid)] = self._read_if_ours(
                        client, uid, folder
                    )
                found.extend(
                    mail
                    for uid in uids
                    if (mail := self._cache.get((folder, key_validity, uid))) is not None
                )
        return sorted(found, key=lambda m: m.sent_at, reverse=True)

    def _read_if_ours(self, client: imaplib.IMAP4, uid: str, folder: str) -> ReceivedEmail | None:
        status, data = client.uid('FETCH', uid, f'(BODY.PEEK[HEADER.FIELDS ({RUN_HEADER})])')
        header = b''.join(part[1] for part in data if isinstance(part, tuple)) if data else b''
        parsed = email.message_from_bytes(header, policy=email.policy.default)
        if status != 'OK' or str(parsed[RUN_HEADER] or '').strip() != self.run:
            return None
        status, data = client.uid('FETCH', uid, '(BODY.PEEK[])')
        raw = b''.join(part[1] for part in data if isinstance(part, tuple)) if data else b''
        return _parse(raw, folder) if status == 'OK' and raw else None

    def purge_other_runs(self) -> int:
        """Delete (flag and expunge) messages that carry an `X-E2E-Run` header of another run --
        only test server mail ever has that header. Returns how many were deleted."""
        deleted = 0
        with self._lock, imap_connect(self.config) as client:
            for folder in self.config.folders:
                status, _ = client.select(_quote_folder(folder))
                if status != 'OK':
                    continue
                status, data = client.uid('SEARCH', 'HEADER', RUN_HEADER, '')
                if status != 'OK' or not data or not data[0]:
                    continue
                for uid in data[0].decode().split():
                    status, fetched = client.uid(
                        'FETCH', uid, f'(BODY.PEEK[HEADER.FIELDS ({RUN_HEADER})])'
                    )
                    header = b''.join(p[1] for p in fetched if isinstance(p, tuple))
                    value = str(
                        email.message_from_bytes(header, policy=email.policy.default)[RUN_HEADER]
                        or ''
                    ).strip()
                    if value and value != self.run:
                        client.uid('STORE', uid, '+FLAGS', '(\\Deleted)')
                        deleted += 1
                client.expunge()
        return deleted


_mailbox: ImapMailbox | None = None


def mailbox() -> ImapMailbox:
    global _mailbox
    if _mailbox is None:
        _mailbox = ImapMailbox(e2e_env.load_mail_config().imap, run_id())
    return _mailbox


# --- Bucket ----------------------------------------------------------------------------------


def bucket_client():
    from apps.core.services.storage import build_client

    return build_client(
        region=e2e_env.S3_REGION,
        endpoint_url=settings.E2E_S3_ENDPOINT_URL,
        access_key_id=e2e_env.S3_ACCESS_KEY,
        secret_access_key=e2e_env.S3_ACCESS_KEY,
        signature_version='s3v4',
    )


CORS_RULES = {
    'CORSRules': [
        {
            # Any origin: the server may be opened as `<name>.localhost` and as a second alias for
            # a signed-out session, and nothing but this local emulator ever has the rule.
            'AllowedOrigins': ['*'],
            'AllowedMethods': ['GET', 'PUT', 'HEAD'],
            'AllowedHeaders': ['*'],
            'ExposeHeaders': ['ETag'],
            'MaxAgeSeconds': 600,
        }
    ]
}


def prepare_bucket(*, empty: bool) -> None:
    bucket = settings.E2E_S3_BUCKET
    if not bucket.startswith(e2e_env.BUCKET_PREFIX):
        raise RuntimeError(f'Refusing to use bucket {bucket!r}: not an e2e bucket.')
    if not e2e_env.is_local_url(settings.E2E_S3_ENDPOINT_URL):
        raise RuntimeError(f'Refusing S3 endpoint {settings.E2E_S3_ENDPOINT_URL}: not local.')
    client = bucket_client()
    existing = {b['Name'] for b in client.list_buckets().get('Buckets', [])}
    if bucket not in existing:
        client.create_bucket(Bucket=bucket)
    elif empty:
        paginator = client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket):
            keys = [{'Key': o['Key']} for o in page.get('Contents', [])]
            if keys:
                client.delete_objects(Bucket=bucket, Delete={'Objects': keys, 'Quiet': True})
    client.put_bucket_cors(Bucket=bucket, CORSConfiguration=CORS_RULES)


# --- Preflight -------------------------------------------------------------------------------


@dataclass
class Check:
    area: str
    name: str
    ok: bool
    detail: str


def _check(results: list[Check], area: str, name: str, func: Callable[[], str]) -> bool:
    try:
        detail = func()
    except Exception as exc:  # every failure is a result, not a crash
        results.append(Check(area, name, False, f'{type(exc).__name__}: {exc}'))
        return False
    results.append(Check(area, name, True, detail))
    return True


def check_database(results: list[Check], *, real: bool) -> None:
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    def connect() -> str:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        name = connection.settings_dict['NAME']
        if real:
            if connection.vendor != 'postgresql':
                raise RuntimeError(f'expected PostgreSQL, got {connection.vendor}')
            host = connection.settings_dict.get('HOST') or 'localhost'
            if host not in e2e_env.LOCAL_HOSTS:
                raise RuntimeError(f'database host {host} is not local')
            if not str(name).startswith(e2e_env.NAME_PREFIX):
                raise RuntimeError(f'database {name} is not an e2e database')
            with connection.cursor() as cursor:
                cursor.execute('SHOW server_version')
                version = cursor.fetchone()[0]
            return f'PostgreSQL {version}, database {name} on {host}'
        return f'{connection.vendor} database {name}'

    def migrations() -> str:
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pending:
            raise RuntimeError(f'{len(pending)} migrations not applied')
        return 'all migrations applied'

    def seeded() -> str:
        from apps.accounts.models import User

        count = User.objects.filter(email__endswith='@example.com', email__startswith='qa.').count()
        if count < 4:
            raise RuntimeError(f'expected the 4 seeded accounts, found {count}')
        return f'{count} qa.* accounts'

    def write() -> str:
        with connection.cursor() as cursor:
            cursor.execute('CREATE TEMPORARY TABLE e2e_preflight (value integer)')
            cursor.execute('INSERT INTO e2e_preflight (value) VALUES (1)')
            cursor.execute('SELECT value FROM e2e_preflight')
            value = cursor.fetchone()[0]
            cursor.execute('DROP TABLE e2e_preflight')
        if value != 1:
            raise RuntimeError('read back a different value')
        return 'create, insert, read and drop work'

    if _check(results, 'Database', 'Connect', connect):
        _check(results, 'Database', 'Migrations', migrations)
        _check(results, 'Database', 'Write', write)
        _check(results, 'Database', 'Seeded accounts', seeded)


def _storages():
    from apps.file_transfer.services import storage as ft_storage
    from apps.receipts.services import storage as receipts_storage
    from apps.todo.services import storage as todo_storage

    return [
        ('File transfer', ft_storage.get_storage(), 'transfers/'),
        ('Receipts', receipts_storage.get_storage(), 'receipts/'),
        ('To-do', todo_storage.get_storage(), 'todo/'),
    ]


def _http(method: str, url: str, *, body: bytes | None = None, headers: dict | None = None):
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def check_storage(results: list[Check], *, real: bool) -> None:
    origin = settings.BASE_URL
    for label, storage, prefix in _storages():
        if not real:
            _check(
                results,
                'S3',
                label,
                lambda s=storage: f'in-memory fake ({type(s).__name__}), no S3 involved',
            )
            continue

        def endpoint(s=storage) -> str:
            url = s.client.meta.endpoint_url
            if not e2e_env.is_local_url(url):
                raise RuntimeError(f'endpoint {url} is not local (Floci)')
            if s.bucket != settings.E2E_S3_BUCKET:
                raise RuntimeError(f'bucket {s.bucket} is not the e2e bucket')
            s.client.head_bucket(Bucket=s.bucket)
            return f'bucket {s.bucket} at {url}'

        def roundtrip(s=storage, p=prefix) -> str:
            key = f'{p}__preflight__/{uuid.uuid4().hex}'
            s.put_object(key, b'preflight')
            data = s.get_object_bytes(key)
            s.delete_objects([key])
            if data != b'preflight':
                raise RuntimeError('read back different bytes')
            return f'put, get and delete under {p}'

        def browser_upload(s=storage, p=prefix) -> str:
            body = f'browser upload {uuid.uuid4().hex}'.encode()
            checksum = base64.b64encode(hashlib.sha256(body).digest()).decode()
            key = f'{p}__preflight__/{uuid.uuid4().hex}'
            url = s.presigned_put_url(key, content_length=len(body), checksum_sha256=checksum)
            status, headers, _ = _http(
                'OPTIONS',
                url,
                headers={
                    'Origin': origin,
                    'Access-Control-Request-Method': 'PUT',
                    'Access-Control-Request-Headers': 'x-amz-checksum-sha256',
                },
            )
            allowed = {k.lower(): v for k, v in headers.items()}
            if status >= 300 or not allowed.get('access-control-allow-origin'):
                raise RuntimeError(f'CORS preflight from {origin} refused (HTTP {status})')
            if 'etag' not in allowed.get('access-control-expose-headers', '').lower():
                raise RuntimeError('CORS rule does not expose ETag (multipart uploads need it)')
            status, _, _ = _http(
                'PUT',
                url,
                body=body,
                headers={'x-amz-checksum-sha256': checksum, 'Origin': origin},
            )
            if status != 200:
                raise RuntimeError(f'presigned PUT answered HTTP {status}')
            bad = base64.b64encode(hashlib.sha256(b'other').digest()).decode()
            status, _, _ = _http(
                'PUT',
                s.presigned_put_url(key + '-bad', content_length=len(body), checksum_sha256=bad),
                body=body,
                headers={'x-amz-checksum-sha256': bad},
            )
            if status == 200:
                raise RuntimeError('a PUT with a wrong checksum was accepted')
            status, _, data = _http('GET', s.presigned_get_url(key, 'preflight.txt'))
            s.delete_objects([key, key + '-bad'])
            if status != 200 or data != body:
                raise RuntimeError(f'presigned GET answered HTTP {status}')
            return 'CORS, presigned PUT (checksum enforced) and presigned GET'

        if _check(results, 'S3', f'{label}: bucket', endpoint):
            _check(results, 'S3', f'{label}: read/write', roundtrip)
            _check(results, 'S3', f'{label}: browser upload', browser_upload)


PROBE_WAIT_SECONDS = 120


def check_mail(results: list[Check], *, real: bool) -> None:
    if not real:
        from tests_e2e import site_fakes

        _check(
            results,
            'Email',
            'Mailbox',
            lambda: f'in-memory mailbox, {len(site_fakes._mailbox)} emails so far',
        )
        return

    config_box: list[e2e_env.MailConfig] = []

    def config() -> str:
        loaded = e2e_env.load_mail_config()
        config_box.append(loaded)
        return (
            f'SMTP {loaded.smtp.host}:{loaded.smtp.port} ({loaded.smtp.security}), '
            f'IMAP {loaded.imap.host}:{loaded.imap.port} ({loaded.imap.security}), '
            f'delivered to {loaded.imap.address}'
        )

    if not _check(results, 'Email', 'emails.yaml', config):
        return
    loaded = config_box[0]

    def smtp_login() -> str:
        with smtp_connect(loaded.smtp) as client:
            client.noop()
        return f'signed in as {loaded.smtp.username}'

    def imap_login() -> str:
        with imap_connect(loaded.imap) as client:
            missing = [
                f
                for f in loaded.imap.folders
                if client.select(_quote_folder(f), readonly=True)[0] != 'OK'
            ]
        if missing:
            raise RuntimeError(f'folders not found: {", ".join(missing)}')
        return f'signed in as {loaded.imap.username}, folders {", ".join(loaded.imap.folders)}'

    smtp_ok = _check(results, 'Email', 'SMTP sign-in', smtp_login)
    imap_ok = _check(results, 'Email', 'IMAP sign-in', imap_login)
    if not (smtp_ok and imap_ok):
        return

    def roundtrip() -> str:
        token = uuid.uuid4().hex[:12]
        link = f'{settings.BASE_URL}/__e2e__/preflight/?probe={token}'
        subject = f'ZipTrigo e2e preflight {token}'
        message = build_message(
            to='preflight@example.com',
            subject=subject,
            text_body=f'Preflight probe. Link: {link}\n',
            html_body=f'<p>Preflight probe. <a href="{link}">Link</a></p>',
            sender=loaded.smtp.sender,
            run=run_id(),
        )
        start = time.monotonic()
        with smtp_connect(loaded.smtp) as client:
            client.send_message(
                message, from_addr=loaded.smtp.sender, to_addrs=[loaded.imap.address]
            )
        box = mailbox()
        while time.monotonic() - start < PROBE_WAIT_SECONDS:
            for mail in box.fetch():
                if mail.subject == subject:
                    if link not in mail.text_body or link not in (mail.html_body or ''):
                        raise RuntimeError('the probe arrived but its link was changed')
                    seconds = time.monotonic() - start
                    return f'sent and read back in {seconds:.0f} s (folder {mail.folder})'
            time.sleep(3)
        raise RuntimeError(
            f'sent, but not found over IMAP after {PROBE_WAIT_SECONDS} s; check the spam folder '
            '(add it to imap.folders)'
        )

    _check(results, 'Email', 'Send and receive', roundtrip)


def run_checks(*, real: bool, mail: bool = True) -> list[Check]:
    results: list[Check] = []
    check_database(results, real=real)
    check_storage(results, real=real)
    if mail:
        check_mail(results, real=real)
    return results
