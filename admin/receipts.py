#!python
"""
Receipts CLI

Command line interface for the receipts JWT API (`/api/receipts/`, issue #98 phase 4), on the model
of `admin/filetransfer.py`: log in, upload receipts and invoices (PDF, JPG, PNG, HEIC, WebP) with
direct-to-S3 uploads, list and inspect them, export a CSV, download all their files as zip files
(`export-files` and its companions, issue #315), look after the vendors the receipts are filed
under (list, rename, vet, merge), and see or regenerate the inbox address receipts can be emailed
to.
"""

import base64
import hashlib
import math
import shlex
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .utils import (
    EnvironmentAnnotation,
    api_url,
    current_environment,
    get_headers,
    logger,
    password_from_prompt_or_env,
    save_token,
    select_environment,
    token_file,
)

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)

# The token lives in `~/.receipts_token.<environment>` (`admin.utils.token_file`); the single
# `~/.receipts_token` of earlier versions is no longer read: log in again, then delete it.
CLI = 'receipts'

# Ordinary API calls; not the PUT of a file -- see `FILE_UPLOAD_TIMEOUT_SECONDS`.
REQUEST_TIMEOUT_SECONDS = 30
# A receipt can be up to `ReceiptsSettings.max_file_bytes` (15 MB by default); a slow connection
# legitimately needs longer than an ordinary API call to finish PUTting one.
FILE_UPLOAD_TIMEOUT_SECONDS = 300
# Bounded retry for a 429 from the API's own rate limits (`RECEIPTS_UPLOAD_USER` and friends): a
# folder of receipts legitimately calls `uploads` often enough to hit one, and that rule's window
# is a minute. The hourly rules (export, regenerate, re-run, login) are different: waiting out
# `Retry-After` there would hang the command for up to an hour, so a wait longer than
# `MAX_RETRY_AFTER_SECONDS` isn't slept on at all (the error says when to try again instead), and
# one call never sleeps more than `MAX_TOTAL_RETRY_WAIT_SECONDS` in all.
MAX_RATE_LIMIT_RETRIES = 5
MAX_RETRY_AFTER_SECONDS = 60
MAX_TOTAL_RETRY_WAIT_SECONDS = 120
# Read a file for hashing in chunks rather than all at once.
HASH_CHUNK_BYTES = 1024 * 1024
# Exports (issue #315): how often to ask whether it is ready (3 s at first, backing off by half
# again each time to at most 15 s: a zip file takes far longer to build than 3 s), how long to wait
# for it by default (minutes), and the chunks a zip file is written in. A zip file is about 100 MB.
EXPORT_POLL_SECONDS = 3
EXPORT_POLL_MAX_SECONDS = 15
EXPORT_WAIT_MINUTES = 30
DOWNLOAD_CHUNK_BYTES = 256 * 1024
#: What `--layout` accepts -> what the API calls it.
EXPORT_LAYOUTS = {'year-vendor': 'year_vendor', 'vendor-year': 'vendor_year'}

#: What the API accepts (`ReceiptsSettings.allowed_types`, checked by the server again against the
#: file's contents): file extension -> content type.
CONTENT_TYPES = {
    '.pdf': 'application/pdf',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
    '.heic': 'image/heic',
    '.heif': 'image/heic',
}


def _parse_retry_after(response) -> float | None:
    """The response's `Retry-After` in seconds, or `None` if it has none (or not a number)."""
    try:
        return max(0.0, float(response.headers.get('Retry-After')))
    except TypeError, ValueError:
        return None


def _retry_after_seconds(response) -> float:
    parsed = _parse_retry_after(response)
    return 1.0 if parsed is None else parsed


def _format_wait(seconds: float) -> str:
    """`45 seconds`, `1 minute`, `13 minutes` (rounded up)."""
    if seconds < 60:
        count, unit = math.ceil(seconds), 'second'
    else:
        count, unit = math.ceil(seconds / 60), 'minute'
    return f'{count} {unit}{"" if count == 1 else "s"}'


class NetworkError(Exception):
    """A request never got a response (refused connection, timeout, ...); the message says why.
    Only raised for a call that asked for it (`_request(..., exit_on_error=False)`)."""


def _request(method: str, url: str, *, exit_on_error: bool = True, **kwargs):
    """`requests.<method>(url, **kwargs)`, with a default timeout (`REQUEST_TIMEOUT_SECONDS`,
    overridable per call), a bounded retry on 429 and one place that handles a request that never
    got an answer -- every HTTP call this CLI makes goes through here rather than calling
    `requests` directly, so all three apply uniformly.

    A 429 is retried after its `Retry-After` only when that is short (`MAX_RETRY_AFTER_SECONDS`)
    and the call's total waiting stays within `MAX_TOTAL_RETRY_WAIT_SECONDS`; otherwise the 429
    response is returned as it is, and `_error_text` tells the user when to try again.

    A `requests` exception (connection refused, timeout, ...) exits 1 with a message, or with
    `exit_on_error=False` raises `NetworkError` for a caller (the upload loop) that carries on.
    """
    import requests

    kwargs.setdefault('timeout', REQUEST_TIMEOUT_SECONDS)
    func = getattr(requests, method)

    def send():
        try:
            return func(url, **kwargs)
        except requests.exceptions.RequestException as e:
            if not exit_on_error:
                raise NetworkError(str(e)) from e
            logger.error(f'Request failed: {e}')
            raise typer.Exit(1) from e

    response = send()
    waited = 0.0
    for _attempt in range(MAX_RATE_LIMIT_RETRIES):
        if response.status_code != 429:
            return response
        wait = _retry_after_seconds(response)
        if wait > MAX_RETRY_AFTER_SECONDS or waited + wait > MAX_TOTAL_RETRY_WAIT_SECONDS:
            return response
        logger.info(f'Rate limited; waiting {wait:.0f}s before retrying...')
        response.close()  # a streamed response holds its connection until it is closed
        time.sleep(wait)
        waited += wait
        response = send()
    return response


def _detail_text(detail) -> str:
    """The API's `detail` as one readable line: a string as it is, and ninja's 422 list of
    `{loc, msg, ...}` as `field: message` pairs (the `body`/`query`/`path` prefix dropped)."""
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if not (isinstance(item, dict) and 'msg' in item):
                parts.append(str(item))
                continue
            loc = list(item.get('loc') or [])
            if loc and loc[0] in ('body', 'query', 'path'):
                was_body = loc.pop(0) == 'body'
                if was_body and loc and loc[0] == 'payload':
                    loc.pop(0)
            where = '.'.join(str(part) for part in loc)
            parts.append(f'{where}: {item["msg"]}' if where else str(item['msg']))
        return '; '.join(parts)
    return str(detail)


def _error_text(response, *, hints: bool = True) -> str:
    """`<status>: <message>`, the message being the API's own `{"detail": ...}` (every error
    response in this API -- `ninja.errors.HttpError`, the site-wide 429 handler in
    `config/api.py` and ninja's 422 -- carries one) rather than just the bare status code. A body
    that isn't JSON, or isn't an object with a `detail`, is shown as the raw text instead.
    `hints` adds what to do about a 401 or 402 (not wanted when logging in)."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and 'detail' in body:
        detail = _detail_text(body['detail'])
    else:
        detail = response.text
    text = f'{response.status_code}: {detail}'
    if response.status_code == 429:
        wait = _parse_retry_after(response)
        if wait is not None:
            text += f' (rate limited, try again in {_format_wait(wait)})'
    elif hints and response.status_code == 401:
        text += ' (run `login` again)'
    elif hints and response.status_code == 402:
        text += ' (top up your credits on the website)'
    return text


def _raise_for_api_error(response, *, hints: bool = True) -> None:
    """Exit non-zero with the API's own error message unless the response is a success."""
    if response.ok:
        return
    logger.error(_error_text(response, hints=hints))
    raise typer.Exit(1)


def _sha256_base64_file(path: Path) -> str:
    """Base64-encoded SHA-256 digest of the file at `path`, read in chunks. This is the value S3
    checks the upload against (`x-amz-checksum-sha256`) and the API binds into the presigned URL."""
    digest = hashlib.sha256()
    with path.open('rb') as fh:
        while chunk := fh.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return base64.b64encode(digest.digest()).decode()


def _content_type(path: Path) -> str | None:
    """The content type to claim for `path`, or `None` for a type the API doesn't accept."""
    return CONTENT_TYPES.get(path.suffix.lower())


def _collect_files(paths: list[Path]) -> list[Path]:
    """Expand files and folders (recursively, sorted for deterministic output) into the flat list
    of files to upload.

    A file with an extension the API doesn't accept, or an empty one, is skipped with a warning
    rather than aborting the whole batch -- a folder of scans easily holds a stray `.txt` or
    `.DS_Store`. A path that doesn't exist is an error.
    """
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            candidates = sorted(p for p in path.rglob('*') if p.is_file())
        elif path.is_file():
            candidates = [path]
        else:
            logger.error(f'Not found: {path}')
            raise typer.Exit(1)
        for candidate in candidates:
            if _content_type(candidate) is None:
                logger.warning(f'Skipping unsupported file type: {candidate}')
            elif candidate.stat().st_size == 0:
                logger.warning(f'Skipping empty file: {candidate}')
            else:
                found.append(candidate)
    return found


class UploadFailed(Exception):
    """One file's upload failed; the message says why. The batch carries on with the next file."""


def _upload_receipt(headers: dict, path: Path) -> dict:
    """Upload one file: `POST /uploads` for a presigned URL, `PUT` the bytes to it with the
    headers the API returned, then `POST /uploads/{id}/complete`. Returns the receipt.

    Raises:
        UploadFailed: the API refused the file (with its message), or the `PUT` failed.
    """
    content_type = _content_type(path)
    assert content_type is not None  # `_collect_files` only lets supported files through
    size = path.stat().st_size
    start = _request(
        'post',
        api_url('/receipts/uploads'),
        json={
            'filename': path.name,
            'size': size,
            'content_type': content_type,
            'checksum_sha256': _sha256_base64_file(path),
        },
        headers=headers,
        exit_on_error=False,
    )
    if not start.ok:
        raise UploadFailed(_error_text(start))
    ticket = start.json()

    try:
        put = _request(
            'put',
            ticket['url'],
            data=path.read_bytes(),
            headers=ticket['headers'],
            timeout=FILE_UPLOAD_TIMEOUT_SECONDS,
            exit_on_error=False,
        )
    except NetworkError as e:
        raise UploadFailed(f'upload failed: {e}') from e
    if not put.ok:
        raise UploadFailed(f'the upload to storage failed ({put.status_code})')

    complete = _request(
        'post',
        api_url(f'/receipts/uploads/{ticket["receipt_id"]}/complete'),
        headers=headers,
        exit_on_error=False,
    )
    if not complete.ok:
        raise UploadFailed(_error_text(complete))
    return complete.json()


def _amount(receipt: dict) -> str:
    if receipt['amount'] is None:
        return ''
    return f'{receipt["amount"]} {receipt["currency"]}'.strip()


def _vendor_text(receipt: dict) -> str:
    """The receipt's vendor with whether the user has vetted it (`Staples (vetted)`); just the
    name, or nothing, for a receipt without a vendor."""
    name = receipt['vendor']
    if not name or receipt.get('vendor_id') is None:
        return name
    return f'{name} ({"vetted" if receipt.get("vendor_vetted") else "not vetted"})'


def _same_name(a: str, b: str) -> bool:
    return ' '.join(a.casefold().split()) == ' '.join(b.casefold().split())


def _read_as(receipt: dict) -> str:
    """The name the AI read on the receipt, when it differs from the vendor's name (the vendor
    was renamed or the receipt moved to another one); empty otherwise."""
    extracted = receipt.get('vendor_extracted') or ''
    return '' if _same_name(extracted, receipt['vendor']) else extracted


@app.command(name='login')
def receipts_login(
    environment: EnvironmentAnnotation,
    email: str,
    password: str | None = typer.Argument(
        None, help='Password. Omit to be prompted, or set RECEIPTS_PASSWORD.'
    ),
):
    """
    Authenticate with the API and store the token.

    Example:
        receipts login local me@example.com
        receipts login prod me@example.com mypassword
    """
    select_environment(environment)
    password = password_from_prompt_or_env(password, 'RECEIPTS_PASSWORD')

    response = _request('post', api_url('/auth/login'), json={'email': email, 'password': password})
    _raise_for_api_error(response, hints=False)
    try:
        save_token(CLI, response.json()['access_token'])
    except OSError as e:
        logger.error(f'Could not save the token to {token_file(CLI)}: {e}')
        raise typer.Exit(1) from e
    logger.info('Successfully authenticated!')


@app.command(name='list')
def receipts_list(
    environment: EnvironmentAnnotation,
    status: str | None = typer.Option(
        None,
        '--status',
        help='needs_review, processed, failed, received, processing, waiting_for_credits, '
        'or in_progress (comma-separated for several).',
    ),
    category: str | None = typer.Option(
        None, '--category', help='A category id, or "none" for uncategorized.'
    ),
    vendor: int | None = typer.Option(
        None, '--vendor', help='A vendor id (see the `vendors` command).'
    ),
    q: str = typer.Option('', '--q', help='Search the vendor, the notes and the amount.'),
    date_from: str | None = typer.Option(
        None, '--from', help='Earliest receipt date, YYYY-MM-DD (inclusive).'
    ),
    date_to: str | None = typer.Option(
        None, '--to', help='Latest receipt date, YYYY-MM-DD (inclusive).'
    ),
    sort: str = typer.Option(
        '-receipt_date',
        '--sort',
        help='receipt_date, -receipt_date, created_at or -created_at.',
    ),
    duplicates: bool = typer.Option(
        False, '--duplicates', help='Only receipts flagged as possible duplicates.'
    ),
    trashed: bool = typer.Option(False, '--trashed', help='List the Trash instead.'),
    limit: int = typer.Option(20, '--limit', help='Max receipts to show (1-100).'),
    offset: int = typer.Option(0, '--offset', help='Number of receipts to skip.'),
):
    """
    List your receipts.

    Example:
        receipts list local
        receipts list local --status needs_review
        receipts list local --vendor 12
        receipts list local --from 2026-10-01 --to 2026-10-31 --q staples --limit 50
    """
    select_environment(environment)
    params: dict = {'sort': sort, 'limit': limit, 'offset': offset}
    for name, value in (
        ('status', status),
        ('category', category),
        ('vendor', vendor),
        ('q', q),
        ('date_from', date_from),
        ('date_to', date_to),
    ):
        if value:
            params[name] = value
    if duplicates:
        params['duplicates'] = 'true'
    if trashed:
        params['trashed'] = 'true'

    response = _request(
        'get', api_url('/receipts/receipts'), params=params, headers=get_headers(CLI)
    )
    _raise_for_api_error(response)
    body = response.json()
    receipts = body['results']

    if not receipts:
        logger.info('No receipts found.')
        return

    table = Table(title='Your Receipts')
    table.add_column('ID', style='cyan', no_wrap=True)
    table.add_column('Date', style='white', no_wrap=True)
    table.add_column('Vendor', style='white')
    table.add_column('Amount', style='magenta', justify='right')
    table.add_column('Category', style='blue')
    table.add_column('Status', style='green', no_wrap=True)
    for receipt in receipts:
        category_name = receipt['category']['name'] if receipt['category'] else ''
        table.add_row(
            receipt['id'],
            receipt['receipt_date'] or '',
            receipt['vendor'] or receipt['original_filename'],
            _amount(receipt),
            category_name,
            receipt['status'],
        )
    Console().print(table)

    shown_through = body['offset'] + len(receipts)
    if shown_through < body['count']:
        logger.info(
            f'Showing {body["offset"] + 1}-{shown_through} of {body["count"]}. '
            f'Use --offset {shown_through} to see more.'
        )


@app.command(name='upload')
def receipts_upload(
    environment: EnvironmentAnnotation,
    paths: list[Path] = typer.Argument(
        ..., help='Receipt files (PDF, JPG, PNG, HEIC, WebP) or folders of them.'
    ),
):
    """
    Upload receipts and invoices: each file goes straight to storage, then is queued for reading.
    Files of another type are skipped with a warning; a file that fails doesn't stop the rest
    (the exit code is non-zero if any did).

    Example:
        receipts upload local invoice.pdf photo.jpg
        receipts upload prod ./october-scans
    """
    select_environment(environment)
    headers = get_headers(CLI)

    files = _collect_files(list(paths))
    if not files:
        logger.error('No files to upload.')
        raise typer.Exit(1)

    failed = 0
    for path in files:
        try:
            receipt = _upload_receipt(headers, path)
        except (UploadFailed, NetworkError) as e:
            failed += 1
            logger.error(f'{path.name}: {e}')
        else:
            logger.info(f'{path.name}: uploaded as {receipt["id"]} ({receipt["status"]})')

    logger.info(f'{len(files) - failed} of {len(files)} uploaded.')
    if failed:
        raise typer.Exit(1)


@app.command(name='get')
def receipts_get(environment: EnvironmentAnnotation, receipt_id: str):
    """
    Show everything about one receipt.

    Example:
        receipts get local 1b6e2f4a-...
    """
    select_environment(environment)

    response = _request(
        'get', api_url(f'/receipts/receipts/{receipt_id}'), headers=get_headers(CLI)
    )
    _raise_for_api_error(response)
    receipt = response.json()

    console = Console()
    category = receipt['category']['name'] if receipt['category'] else ''
    rows = [
        ('ID', receipt['id']),
        ('Status', receipt['status']),
        ('Failure', receipt['failure_reason']),
        ('Vendor', _vendor_text(receipt)),
        ('Vendor ID', str(receipt.get('vendor_id') or '')),
        ('Read as', _read_as(receipt)),
        ('Amount', _amount(receipt)),
        ('Receipt date', receipt['receipt_date'] or ''),
        ('Type', receipt['document_type']),
        ('Number', receipt['document_number']),
        ('Payment method', receipt['payment_method']),
        ('Category', category),
        ('Notes', receipt['notes']),
        ('Missing', ', '.join(receipt['missing_fields'])),
        ('Possible duplicate of', receipt['possible_duplicate_of'] or ''),
        ('Source', receipt['source']),
        ('File', f'{receipt["original_filename"]} ({receipt["size_bytes"]} bytes)'),
        ('Added', receipt['created_at']),
        ('Updated', receipt['updated_at']),
        ('Processed', receipt['processed_at'] or ''),
        ('In the trash since', receipt['deleted_at'] or ''),
    ]
    for label, value in rows:
        if value:
            console.print(f'[cyan]{label}:[/cyan] {value}', highlight=False)


@app.command(name='export')
def receipts_export(
    environment: EnvironmentAnnotation,
    out: Path = typer.Option(..., '--out', '-o', help='Where to write the CSV.'),
    status: str | None = typer.Option(None, '--status', help='As for `list`.'),
    category: str | None = typer.Option(None, '--category', help='As for `list`.'),
    vendor: int | None = typer.Option(None, '--vendor', help='As for `list`.'),
    q: str = typer.Option('', '--q', help='Search the vendor, the notes and the amount.'),
    date_from: str | None = typer.Option(None, '--from', help='Earliest receipt date.'),
    date_to: str | None = typer.Option(None, '--to', help='Latest receipt date.'),
    sort: str = typer.Option('-receipt_date', '--sort', help='As for `list`.'),
    duplicates: bool = typer.Option(False, '--duplicates', help='Only possible duplicates.'),
):
    """
    Export the matching receipts as a CSV file (the Trash is never exported).

    Example:
        receipts export local --out receipts.csv
        receipts export local --out q3.csv --from 2026-07-01 --to 2026-09-30 --status processed
        receipts export local --out acme.csv --vendor 12
    """
    select_environment(environment)
    params: dict = {'sort': sort}
    for name, value in (
        ('status', status),
        ('category', category),
        ('vendor', vendor),
        ('q', q),
        ('date_from', date_from),
        ('date_to', date_to),
    ):
        if value:
            params[name] = value
    if duplicates:
        params['duplicates'] = 'true'

    headers = get_headers(CLI)
    if not out.parent.is_dir():
        # Checked before the request: an export spends one of the few per-hour exports.
        logger.error(f'Not a folder: {out.parent}')
        raise typer.Exit(1)

    import requests

    response = _request(
        'get', api_url('/receipts/export.csv'), params=params, headers=headers, stream=True
    )
    try:
        _raise_for_api_error(response)
        # Written next to `out` and renamed into place only once it is complete, so a dropped
        # connection never leaves a cut-off CSV at `--out` (or replaces a good older one).
        partial = out.with_name(f'.{out.name}.part')
        size = 0
        try:
            with partial.open('wb') as fh:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    fh.write(chunk)
                    size += len(chunk)
            partial.replace(out)
        except (requests.exceptions.RequestException, OSError) as e:
            partial.unlink(missing_ok=True)
            logger.error(f'Export failed, nothing written to {out}: {e}')
            raise typer.Exit(1) from e
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    finally:
        response.close()
    logger.info(f'Wrote {out} ({size} bytes).')


def _vendor_url(vendor_id: int) -> str:
    return api_url(f'/receipts/vendors/{vendor_id}')


def _vendor_summary(vendor: dict) -> str:
    """`ACME (id 12, vetted, 3 receipts)`: how a vendor is named in a message."""
    count = vendor['receipt_count']
    state = 'vetted' if vendor['vetted'] else 'not vetted'
    return (
        f'{vendor["name"]} (id {vendor["id"]}, {state}, {count} receipt{"" if count == 1 else "s"})'
    )


def _conflict(response) -> dict | None:
    """The body of a 409 from a vendor rename (`conflicting_vendor_id`, ...), or `None`."""
    if response.status_code != 409:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) and 'conflicting_vendor_id' in body else None


@app.command(name='vendors')
def receipts_vendors(
    environment: EnvironmentAnnotation,
    to_vet: bool = typer.Option(
        False, '--to-vet', help='Only vendors you have not vetted (confirmed) yet.'
    ),
    q: str = typer.Option('', '--q', help="Search the vendors' names and the names they go by."),
    limit: int = typer.Option(20, '--limit', help='Max vendors to show (1-100).'),
    offset: int = typer.Option(0, '--offset', help='Number of vendors to skip.'),
):
    """
    List your vendors: the companies your receipts are filed under.

    Example:
        receipts vendors local
        receipts vendors local --to-vet
        receipts vendors local --q acme
    """
    select_environment(environment)
    params: dict = {'status': 'to_vet' if to_vet else 'all', 'limit': limit, 'offset': offset}
    if q:
        params['q'] = q

    response = _request(
        'get', api_url('/receipts/vendors'), params=params, headers=get_headers(CLI)
    )
    _raise_for_api_error(response)
    body = response.json()
    vendors = body['results']

    if not vendors:
        logger.info('No vendors found.')
        return

    table = Table(title='Your Vendors')
    table.add_column('ID', style='cyan', no_wrap=True, justify='right')
    table.add_column('Name', style='white')
    table.add_column('Vetted', style='green', no_wrap=True)
    table.add_column('Receipts', style='magenta', justify='right')
    table.add_column('Last receipt', style='white', no_wrap=True)
    for vendor in vendors:
        trashed = f' (+{vendor["trashed_count"]} in trash)' if vendor['trashed_count'] else ''
        table.add_row(
            str(vendor['id']),
            vendor['name'],
            'yes' if vendor['vetted'] else 'to vet',
            f'{vendor["receipt_count"]}{trashed}',
            vendor['last_receipt_date'] or '',
        )
    Console().print(table)

    shown_through = body['offset'] + len(vendors)
    if shown_through < body['count']:
        logger.info(
            f'Showing {body["offset"] + 1}-{shown_through} of {body["count"]}. '
            f'Use --offset {shown_through} to see more.'
        )


@app.command(name='vendor-rename')
def receipts_vendor_rename(
    environment: EnvironmentAnnotation,
    vendor_id: int = typer.Argument(..., help='The vendor id (see the `vendors` command).'),
    name: str = typer.Argument(..., help='The new name.'),
    not_vetted: bool = typer.Option(
        False, '--not-vetted', help='Leave the vendor unvetted (renaming vets it by default).'
    ),
):
    """
    Rename a vendor: every receipt filed under it shows the new name, and the old name still
    matches it. If another vendor already has that name, nothing changes and the command says
    which, so you can merge the two with `vendor-merge`.

    Example:
        receipts vendor-rename local 12 "ACME, LTD"
    """
    select_environment(environment)
    body: dict = {'name': name}
    if not_vetted:
        body['vetted'] = False

    response = _request('patch', _vendor_url(vendor_id), json=body, headers=get_headers(CLI))
    clash = _conflict(response)
    if clash is not None:
        other_id = clash['conflicting_vendor_id']
        other_name = clash.get('conflicting_vendor_name') or name
        logger.error(
            f'Not renamed: you already have a vendor called {other_name!r} (id {other_id}).'
        )
        logger.error(
            f'To combine them, run: receipts vendor-merge {current_environment()} {other_id} {vendor_id}'
            f' (keeps vendor {other_id}).'
        )
        logger.error(
            f'Then, to use the new spelling: receipts vendor-rename {current_environment()} {other_id} '
            f'{shlex.quote(name)}'
        )
        raise typer.Exit(1)
    _raise_for_api_error(response)
    vendor = response.json()
    logger.info(f'Renamed: {_vendor_summary(vendor)}.')


@app.command(name='vendor-vet')
def receipts_vendor_vet(
    environment: EnvironmentAnnotation,
    vendor_ids: list[int] = typer.Argument(..., help='The vendor ids to mark as vetted.'),
    unvet: bool = typer.Option(False, '--unvet', help='Mark them as not vetted instead.'),
):
    """
    Mark vendors as vetted (confirmed by you), so future receipts are matched to them first. A
    vendor that can't be updated doesn't stop the rest (the exit code is non-zero if any failed).

    Example:
        receipts vendor-vet local 12 13 14
        receipts vendor-vet local 12 --unvet
    """
    select_environment(environment)
    headers = get_headers(CLI)

    failed = 0
    for vendor_id in dict.fromkeys(vendor_ids):
        response = _request(
            'patch', _vendor_url(vendor_id), json={'vetted': not unvet}, headers=headers
        )
        if not response.ok:
            failed += 1
            logger.error(f'Vendor {vendor_id}: {_error_text(response)}')
            continue
        logger.info(f'{"Not vetted" if unvet else "Vetted"}: {_vendor_summary(response.json())}.')

    if failed:
        raise typer.Exit(1)


@app.command(name='vendor-merge')
def receipts_vendor_merge(
    environment: EnvironmentAnnotation,
    keep: int = typer.Argument(..., help='The id of the vendor to keep.'),
    vendor_ids: list[int] = typer.Argument(..., help='The ids of the vendors to merge into it.'),
    yes: bool = typer.Option(False, '--yes', '-y', help='Do not ask for confirmation.'),
):
    """
    Merge vendors into one: their receipts move to the vendor you keep and show its name, the names
    they were known by now match it too, and the merged vendors are deleted. This can't be undone.

    Example:
        receipts vendor-merge local 12 13 14
        receipts vendor-merge local 12 13 --yes
    """
    select_environment(environment)
    headers = get_headers(CLI)
    others = [vendor_id for vendor_id in dict.fromkeys(vendor_ids) if vendor_id != keep]
    if not others:
        logger.error('Give at least one vendor besides the one to keep.')
        raise typer.Exit(1)

    if not yes:
        # Show what the ids are before asking (and fail on a wrong one before the prompt).
        summaries = {}
        for vendor_id in (keep, *others):
            lookup = _request('get', _vendor_url(vendor_id), headers=headers)
            _raise_for_api_error(lookup)
            summaries[vendor_id] = _vendor_summary(lookup.json())
        logger.info(f'Keep:  {summaries[keep]}')
        for vendor_id in others:
            logger.info(f'Merge: {summaries[vendor_id]}')
        if not typer.confirm(
            'Their receipts move to the vendor you keep, and the others are deleted. Merge?'
        ):
            logger.info('Cancelled.')
            raise typer.Exit(1)

    response = _request(
        'post',
        api_url('/receipts/vendors/merge'),
        json={'keep': keep, 'merge': others},
        headers=headers,
    )
    _raise_for_api_error(response)
    logger.info(f'Merged: {_vendor_summary(response.json())}.')


@app.command(name='inbox')
def receipts_inbox(environment: EnvironmentAnnotation):
    """
    Show the address receipts can be emailed to, who may send to it and the default currency.

    Example:
        receipts inbox local
    """
    select_environment(environment)

    response = _request('get', api_url('/receipts/inbox'), headers=get_headers(CLI))
    _raise_for_api_error(response)
    inbox = response.json()

    console = Console()
    console.print(f'[cyan]Address:[/cyan] {inbox["address"]}', highlight=False)
    allowlist = inbox['sender_allowlist']
    console.print(
        f'[cyan]Accepted senders:[/cyan] {", ".join(allowlist) if allowlist else "anyone"}',
        highlight=False,
    )
    console.print(f'[cyan]Default currency:[/cyan] {inbox["default_currency"]}')
    confirmation = inbox['forwarding_confirmation']
    if confirmation:
        console.print(
            f'[cyan]Forwarding confirmation (received {confirmation["received_at"]}):[/cyan] '
            f'code {confirmation["code"]}',
            highlight=False,
        )
        if confirmation['url']:
            console.print(f'  {confirmation["url"]}', highlight=False)


@app.command(name='regenerate')
def receipts_regenerate(
    environment: EnvironmentAnnotation,
    yes: bool = typer.Option(False, '--yes', '-y', help='Do not ask for confirmation.'),
):
    """
    Retire the inbox address and issue a new one. Mail still sent to the old address is dropped.

    Example:
        receipts regenerate local
        receipts regenerate local --yes
    """
    select_environment(environment)
    headers = get_headers(CLI)
    if not yes and not typer.confirm(
        'The current address stops working immediately. Generate a new one?'
    ):
        logger.info('Cancelled.')
        raise typer.Exit(1)

    response = _request('post', api_url('/receipts/inbox/regenerate'), headers=headers)
    _raise_for_api_error(response)
    logger.info(f'New address: {response.json()["address"]}')


# -- Exports: all the files as zip files (issue #315) ---------------------------------------------


def _say(text: str) -> None:
    """A line of output; `text` may hold user text (a vendor's name), so it is not read as Rich
    markup."""
    logger.info(escape(text))


def _fail(text: str) -> typer.Exit:
    """Log an error and return the exception to `raise`."""
    logger.error(escape(text))
    return typer.Exit(1)


def _credits(amount: int) -> str:
    return f'{amount} credit{"" if amount == 1 else "s"}'


def _size_text(size: int) -> str:
    """`884 B`, `12.3 MB` (1,024-based, like the website)."""
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{int(value)} B' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{size} B'  # pragma: no cover - the loop always returns


def _layout_value(layout: str) -> str:
    key = layout.strip().lower().replace('_', '-')
    if key not in EXPORT_LAYOUTS:
        raise _fail('Choose --layout year-vendor or vendor-year.')
    return EXPORT_LAYOUTS[key]


def _export_selection(
    layout: str,
    status: str | None,
    category: str | None,
    vendor: int | None,
    q: str,
    date_from: str | None,
    date_to: str | None,
    duplicates: bool,
) -> dict:
    """The body of `POST /exports/quote` and `POST /exports`: the list's filters and the layout."""
    body: dict = {'layout': _layout_value(layout)}
    for name, value in (
        ('status', status),
        ('category', category),
        ('vendor', vendor),
        ('q', q),
        ('date_from', date_from),
        ('date_to', date_to),
    ):
        if value:
            body[name] = value
    if duplicates:
        body['duplicates'] = True
    return body


def _export_url(export_id: str, suffix: str = '') -> str:
    return api_url(f'/receipts/exports/{export_id}{suffix}')


def _describe_quote(quote: dict) -> None:
    files = quote['file_count']
    _say(
        f'{files} file{"" if files == 1 else "s"} ({_size_text(quote["total_bytes"])}) in '
        f'{quote["part_count"]} zip file{"" if quote["part_count"] == 1 else "s"} of about '
        f'{_size_text(quote["part_bytes"])} each, with a receipts.csv of their data.'
    )
    _say(
        f'Cost: {_credits(quote["cost"])} ({quote["price_per_file"]} per file). '
        f'Your balance is {quote["balance"]}. They stay available for {quote["retention_days"]} '
        'days.'
    )


def _fetch_hint(export_id: str, out: Path) -> str:
    return (
        f'Fetch it later with: receipts export-download {current_environment()} {export_id} '
        f'--out {shlex.quote(str(out))}'
    )


def _refused_creating(response) -> str:
    """Why creating the export was refused with a 409 (nothing was charged): another export is
    being prepared, or the cost is no longer the one that was quoted. The API words are for an API
    client (its cost message says to send `expected_cost`), so a changed cost is put in the CLI's
    own words and says to run the command again for the new quote."""
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = body.get('detail') if isinstance(body, dict) else None
    if isinstance(detail, str) and detail.startswith('The cost changed'):
        return (
            'The cost changed since it was quoted, so nothing was charged. Run the command again '
            'to see the new quote.'
        )
    return (
        f'{_error_text(response).rstrip(".")}. Check `receipts exports {current_environment()}`, then '
        'try again.'
    )


def _wait_for_export(export_id: str, headers: dict, minutes: float, hint: str) -> dict:
    """Poll the export until it is no longer being prepared; returns it. Ctrl-C, running out of
    time and an error that ends the wait all leave it building (it carries on in the background)
    and say how to fetch it later. A dropped connection, a 5xx and a 429 that `_request` gave up
    on are not errors: they are tried again until the time is up."""
    limit = minutes * 60
    waited = 0.0
    delay = float(EXPORT_POLL_SECONDS)
    shown: int | None = None
    _say('Preparing your zip files (Ctrl-C stops waiting; the export carries on without you)...')
    try:
        while True:
            problem: str | None = None
            try:
                response = _request(
                    'get', _export_url(export_id), headers=headers, exit_on_error=False
                )
            except NetworkError as e:
                problem = f'Could not reach the site ({e})'
            else:
                if response.status_code >= 500 or response.status_code == 429:
                    problem = f'The site answered {response.status_code}'
                else:
                    try:
                        _raise_for_api_error(response)
                    except typer.Exit:
                        _say(hint)
                        raise
                    export = response.json()
                    if export['status'] != 'building':
                        return export
                    built = sum(1 for part in export['parts'] if part['built'])
                    if built != shown:
                        shown = built
                        _say(f'Written so far: {built} of {export["part_count"]} zip files.')
            if problem:
                logger.warning(escape(f'{problem}; trying again...'))
            if waited >= limit:
                _say(f'Still not ready after {_format_wait(limit)}. {hint}')
                raise typer.Exit(1)
            time.sleep(delay)
            waited += delay
            delay = min(delay * 1.5, float(EXPORT_POLL_MAX_SECONDS))
    except KeyboardInterrupt:
        _say(f'Stopped waiting. The export is still being prepared. {hint}')
        raise typer.Exit(130) from None


class DownloadFailed(Exception):
    """One zip file could not be downloaded; the message says why. The rest carry on."""


def _download_part(export_id: str, part: dict, target: Path, headers: dict) -> None:
    """Download one zip file: ask the API for a short-lived URL, then stream it **without the API
    token** (it is a presigned storage URL) to `.<name>.part` next to `target`, renamed into place
    only once complete and of the size the export says, so an interrupted download never leaves a
    cut-off zip behind.

    Raises:
        DownloadFailed: with the reason.
    """
    import requests

    try:
        info = _request(
            'get',
            _export_url(export_id, f'/parts/{part["number"]}/url'),
            headers=headers,
            exit_on_error=False,
        )
    except NetworkError as e:
        raise DownloadFailed(str(e)) from e
    if not info.ok:
        raise DownloadFailed(_error_text(info))
    ticket = info.json()
    try:
        response = _request('get', ticket['url'], stream=True, exit_on_error=False)
    except NetworkError as e:
        raise DownloadFailed(f'download failed: {e}') from e
    try:
        if not response.ok:
            raise DownloadFailed(f'the download from storage failed ({response.status_code})')
        partial = target.with_name(f'.{target.name}.part')
        size = 0
        try:
            with partial.open('wb') as fh:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                    fh.write(chunk)
                    size += len(chunk)
            if size != ticket['size_bytes']:
                raise DownloadFailed(f'received {size} bytes, expected {ticket["size_bytes"]}')
            partial.replace(target)
        except (requests.exceptions.RequestException, OSError) as e:
            partial.unlink(missing_ok=True)
            raise DownloadFailed(f'download failed: {e}') from e
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    finally:
        response.close()


def _download_export(
    export: dict,
    out: Path,
    headers: dict,
    *,
    force: bool,
    resume: bool = False,
    paid: bool = False,
) -> None:
    """Download every zip file of a ready export into the folder `out` (created if needed).

    Before anything is downloaded, a file of the same name is refused unless `force` is set
    (`--force` replaces it). With `resume` (`export-download`, for finishing an interrupted run) a
    file that is already complete (the size the export says) is skipped instead of refused, and
    only one of a different size is. `paid` says the export was just created and charged (a clash
    then points at `export-download`, not at making another). Exits 1 if any file failed (the
    others are still downloaded).

    Resuming compares sizes only (the API has no checksum per file), so use a folder per export.
    """
    parts = [part for part in export['parts'] if part['built']]
    if not parts:
        raise _fail('This export has no zip files to download.')
    targets: list[tuple[dict, Path]] = []
    for part in parts:
        name = Path(part['filename']).name
        if name in ('', '.', '..'):
            raise _fail(f'The site gave an unusable file name: {part["filename"]!r}.')
        targets.append((part, out / name))
    clashes = [
        target
        for part, target in targets
        if target.exists()
        and not force
        and not (resume and target.stat().st_size == part['size_bytes'])
    ]
    if clashes:
        listing = ', '.join(str(target) for target in clashes)
        if paid:
            raise _fail(
                f'{listing} already exists. The export is paid for and ready: run `receipts '
                f'export-download {current_environment()} {export["id"]} --out <a folder for this '
                'export>` (add --force to replace the existing file) rather than exporting '
                'again, which would charge a second time.'
            )
        raise _fail(f'{listing} already exists. Use --force to replace it.')
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise _fail(f'Could not create {out}: {e}') from e

    failed = 0
    try:
        for part, target in targets:
            if resume and not force and target.exists():
                _say(f'{target.name}: already downloaded, skipped.')
                continue
            _say(f'{target.name}: downloading {_size_text(part["size_bytes"])}...')
            try:
                _download_part(export['id'], part, target, headers)
            except DownloadFailed as e:
                failed += 1
                logger.error(escape(f'{target.name}: {e}'))
            else:
                _say(f'Wrote {target}.')
    except KeyboardInterrupt:
        # The part being written was cleaned up; the export is paid for and ready, so say how to
        # get the rest without paying again (as Ctrl-C while waiting does).
        _say(
            f'Stopped downloading. The export is ready and paid for. {_fetch_hint(export["id"], out)}'
        )
        raise typer.Exit(130) from None
    if failed:
        raise _fail(
            f'{failed} of {len(targets)} zip files failed. Run `receipts export-download '
            f'{current_environment()} {export["id"]} --out {shlex.quote(str(out))}` to get the '
            'rest.'
        )
    _say(f'Downloaded {len(targets)} zip file{"" if len(targets) == 1 else "s"} to {out}.')


@app.command(name='export-files')
def receipts_export_files(
    environment: EnvironmentAnnotation,
    layout: str = typer.Option(
        'year-vendor',
        '--layout',
        help='How the files are organized in the zip files: year-vendor (2026/Acme/x.pdf) or '
        'vendor-year (Acme/2026/x.pdf).',
    ),
    status: str | None = typer.Option(None, '--status', help='As for `list`.'),
    category: str | None = typer.Option(None, '--category', help='As for `list`.'),
    vendor: int | None = typer.Option(None, '--vendor', help='As for `list`.'),
    q: str = typer.Option('', '--q', help='Search the vendor, the notes and the amount.'),
    date_from: str | None = typer.Option(None, '--from', help='Earliest receipt date.'),
    date_to: str | None = typer.Option(None, '--to', help='Latest receipt date.'),
    duplicates: bool = typer.Option(False, '--duplicates', help='Only possible duplicates.'),
    out: Path = typer.Option(
        Path('.'), '--out', '-o', help='The folder to save the zip files in (created if needed).'
    ),
    yes: bool = typer.Option(
        False, '--yes', '-y', help='Do not ask to confirm the cost (and accept whatever it is).'
    ),
    force: bool = typer.Option(False, '--force', help='Replace zip files that already exist.'),
    wait: float = typer.Option(
        EXPORT_WAIT_MINUTES, '--wait', help='Minutes to wait for the zip files to be prepared.'
    ),
):
    """
    Download the files of your receipts (the ones the filters match; the Trash never) as zip files:
    each of about 100 MB, independent, the first with a receipts.csv of their data. It costs
    credits per file, so the cost is shown and you are asked to confirm it first. The zip files are
    prepared in the background; this command waits for them and downloads them. Ctrl-C stops
    waiting without cancelling the export. Use a folder of its own for each export: files are told
    apart by name and size only.

    Example:
        receipts export-files local --out ./my-receipts
        receipts export-files local --out ./2026 --from 2026-01-01 --to 2026-12-31 --layout vendor-year
        receipts export-files prod --vendor 12 --yes
    """
    select_environment(environment)
    headers = get_headers(CLI)
    body = _export_selection(layout, status, category, vendor, q, date_from, date_to, duplicates)
    if out.exists() and not out.is_dir():
        raise _fail(f'Not a folder: {out}')
    # Before anything is quoted or charged: a folder that already holds zip files of an export
    # would clash with the new ones (their names are receipts-<date>[-part-NN-of-MM].zip).
    existing = sorted(out.glob('receipts-*.zip')) if out.is_dir() else []
    if existing and not force:
        raise _fail(
            f'{out} already holds {", ".join(p.name for p in existing)}. Use an empty folder '
            '(--out), or --force to replace files of the same name. Nothing was charged.'
        )

    quote_response = _request(
        'post', api_url('/receipts/exports/quote'), json=body, headers=headers
    )
    _raise_for_api_error(quote_response)
    quote = quote_response.json()
    if quote['file_count'] == 0:
        raise _fail('There are no files to download with these filters.')
    _describe_quote(quote)
    if quote['frozen']:
        raise _fail('Your receipts are paused until you add credits.')
    if not quote['affordable']:
        raise _fail(
            f'That costs {_credits(quote["cost"])} and your balance is {quote["balance"]}. '
            'Top up your credits on the website.'
        )

    # `--yes` accepts whatever it costs (no `expected_cost` is sent); otherwise the amount that was
    # shown and confirmed is sent, so a cost that changed meanwhile is refused, not charged.
    create_body = body
    if not yes:
        if quote['cost'] > 0 and not typer.confirm(
            f'This costs {_credits(quote["cost"])}. Continue?'
        ):
            raise _fail('Cancelled; nothing was charged.')
        create_body = {**body, 'expected_cost': quote['cost']}

    created = _request('post', api_url('/receipts/exports'), json=create_body, headers=headers)
    if created.status_code == 409:
        # Another export is being prepared, or the cost changed since it was quoted: either way
        # nothing was charged, and the message says which.
        raise _fail(_refused_creating(created))
    _raise_for_api_error(created)
    export = created.json()
    if export['charged_credits']:
        _say(f'Charged {_credits(export["charged_credits"])}.')
    _say(f'Export {export["id"]} started.')

    export = _wait_for_export(export['id'], headers, wait, _fetch_hint(export['id'], out))
    if export['status'] != 'ready':
        refunded = export['refunded_credits']
        raise _fail(
            f'The export {export["status"]}'
            + (f' and {_credits(refunded)} were refunded.' if refunded else '.')
        )
    if export['skipped_count']:
        _say(
            f'{export["skipped_count"]} file(s) had vanished and were left out '
            f'({_credits(export["refunded_credits"])} refunded).'
        )
    _download_export(export, out, headers, force=force, paid=True)


@app.command(name='exports')
def receipts_exports(
    environment: EnvironmentAnnotation,
    limit: int = typer.Option(20, '--limit', help='Max exports to show (1-100).'),
    offset: int = typer.Option(0, '--offset', help='Number of exports to skip.'),
):
    """
    List your exports of files (see `export-files`), newest first.

    Example:
        receipts exports local
    """
    select_environment(environment)
    response = _request(
        'get',
        api_url('/receipts/exports'),
        params={'limit': limit, 'offset': offset},
        headers=get_headers(CLI),
    )
    _raise_for_api_error(response)
    body = response.json()
    exports = body['results']
    if not exports:
        logger.info('No exports.')
        return

    table = Table(title='Your Exports')
    table.add_column('ID', style='cyan', no_wrap=True)
    table.add_column('Created', style='white', no_wrap=True)
    table.add_column('Status', style='green', no_wrap=True)
    table.add_column('Files', style='white', justify='right')
    table.add_column('Charged', style='magenta', justify='right')
    table.add_column('Expires', style='white', no_wrap=True)
    for export in exports:
        table.add_row(
            export['id'],
            export['created_at'][:10],
            export['status'],
            str(export['file_count']),
            str(export['charged_credits']),
            (export['expires_at'] or '')[:10],
        )
    Console().print(table)

    shown_through = body['offset'] + len(exports)
    if shown_through < body['count']:
        logger.info(
            f'Showing {body["offset"] + 1}-{shown_through} of {body["count"]}. '
            f'Use --offset {shown_through} to see more.'
        )


@app.command(name='export-get')
def receipts_export_get(environment: EnvironmentAnnotation, export_id: str):
    """
    Show one export: its status, what it was made from, the cost and its zip files.

    Example:
        receipts export-get local 1b6e2f4a-...
    """
    select_environment(environment)
    response = _request('get', _export_url(export_id), headers=get_headers(CLI))
    _raise_for_api_error(response)
    export = response.json()

    console = Console()
    selection = '; '.join(export['filters']['summary']) or 'everything'
    files = f'{export["file_count"]}'
    if export['skipped_count']:
        files += f' ({export["skipped_count"]} left out: gone when zipped)'
    ready_parts = sum(1 for part in export['parts'] if part['built'])
    rows = [
        ('ID', export['id']),
        ('Status', export['status']),
        ('Selection', selection),
        ('Layout', export['layout']),
        ('Files', files),
        ('Size', _size_text(export['total_bytes'])),
        ('Zip files', f'{export["part_count"]} ({ready_parts} written)'),
        ('Charged', _credits(export['charged_credits'])),
        ('Refunded', _credits(export['refunded_credits']) if export['refunded_credits'] else ''),
        ('Created', export['created_at']),
        ('Ready', export['ready_at'] or ''),
        ('Expires', export['expires_at'] or ''),
    ]
    for label, value in rows:
        if value:
            console.print(f'[cyan]{label}:[/cyan] {escape(str(value))}', highlight=False)

    if export['parts']:
        table = Table(title='Zip files')
        table.add_column('#', style='cyan', justify='right', no_wrap=True)
        table.add_column('Name', style='white')
        table.add_column('Files', style='white', justify='right')
        table.add_column('Size', style='white', justify='right')
        table.add_column('Written', style='green', no_wrap=True)
        for part in export['parts']:
            table.add_row(
                str(part['number']),
                part['filename'],
                str(part['file_count']),
                _size_text(part['size_bytes']) if part['built'] else '',
                'yes' if part['built'] else 'no',
            )
        console.print(table)


@app.command(name='export-download')
def receipts_export_download(
    environment: EnvironmentAnnotation,
    export_id: str,
    out: Path = typer.Option(
        Path('.'), '--out', '-o', help='The folder to save the zip files in (created if needed).'
    ),
    force: bool = typer.Option(False, '--force', help='Replace zip files that already exist.'),
    wait: float = typer.Option(
        EXPORT_WAIT_MINUTES, '--wait', help='Minutes to wait if it is still being prepared.'
    ),
):
    """
    Download the zip files of an export (waits for it if it is still being prepared). Each is
    written to a temporary file and renamed when complete. A file that is already there with the
    right size is skipped, so an interrupted download can be finished by running this again; one
    with a different size is refused unless you pass --force. Use a folder of its own for each
    export: files are told apart by name and size only (there is no checksum), so a zip file of
    another export with the same name and size would be taken for this one. No credits are charged.

    Example:
        receipts export-download local 1b6e2f4a-... --out ./my-receipts
    """
    select_environment(environment)
    headers = get_headers(CLI)
    if out.exists() and not out.is_dir():
        raise _fail(f'Not a folder: {out}')
    response = _request('get', _export_url(export_id), headers=headers)
    _raise_for_api_error(response)
    export = response.json()
    if export['status'] == 'building':
        export = _wait_for_export(export_id, headers, wait, _fetch_hint(export_id, out))
    if export['status'] != 'ready':
        raise _fail(
            f'This export is {export["status"]}, so there is nothing to download. '
            'Start a new one with `export-files`.'
        )
    _download_export(export, out, headers, force=force, resume=True)


@app.command(name='export-delete')
def receipts_export_delete(
    environment: EnvironmentAnnotation,
    export_id: str,
    yes: bool = typer.Option(False, '--yes', '-y', help='Do not ask for confirmation.'),
):
    """
    Delete an export: its zip files are removed now. Nothing is refunded. An export that is still
    being prepared can't be deleted.

    Example:
        receipts export-delete local 1b6e2f4a-...
    """
    select_environment(environment)
    headers = get_headers(CLI)
    if not yes and not typer.confirm(
        'The zip files are deleted now and nothing is refunded. Delete?'
    ):
        raise _fail('Cancelled; nothing was deleted.')
    response = _request('delete', _export_url(export_id), headers=headers)
    _raise_for_api_error(response)
    _say('Deleted.')


if __name__ == '__main__':
    app()
