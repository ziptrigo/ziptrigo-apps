#!python
"""
Receipts CLI

Command line interface for the receipts JWT API (`/api/receipts/`, issue #98 phase 4), on the model
of `admin/filetransfer.py`: log in, upload receipts and invoices (PDF, JPG, PNG, HEIC, WebP) with
direct-to-S3 uploads, list and inspect them, export a CSV, look after the vendors the receipts are
filed under (list, rename, vet, merge), and see or regenerate the inbox address receipts can be
emailed to.
"""

import base64
import hashlib
import math
import os
import shlex
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .utils import EnvironmentAnnotation, logger, set_environment, write_private_file

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)

# A separate token file from `admin/qrcode.py` and `admin/filetransfer.py`: each CLI logs in
# independently (possibly as a different account), so sharing one file would mean the last `login`
# of any of them silently signs the others out too. And one per environment
# (`~/.receipts_token.local`, `.prod`, ...; see `token_file`): a token is only valid for the server
# that issued it, so a shared file would send the prod token to the local server and the other way
# round. The single `~/.receipts_token` of earlier versions is no longer read: log in again.
TOKEN_FILE_BASE = Path.home() / '.receipts_token'

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


def _api_url(path: str) -> str:
    """`<site>/api/receipts<path>`. The site comes from `API_BASE_URL`, else the selected
    environment file's `BASE_URL` (both read now, after `set_environment` has loaded that file --
    not at import time), else the local dev server."""
    base = os.environ.get('API_BASE_URL') or os.environ.get('BASE_URL') or 'http://localhost:8000'
    return f'{base.rstrip("/")}/api{path}'


# The environment the running command selected (`_select_environment`); it names the token file.
_environment = 'local'


def _select_environment(environment) -> None:
    """Load the selected environment file (`set_environment`) and remember which environment it
    is, for `token_file`."""
    global _environment
    selected = set_environment(environment)
    _environment = str(selected or os.environ.get('ENVIRONMENT') or 'local')


def token_file() -> Path:
    """This environment's token file, `~/.receipts_token.<environment>`."""
    return TOKEN_FILE_BASE.with_name(f'{TOKEN_FILE_BASE.name}.{_environment}')


def get_token() -> str | None:
    """Retrieve stored authentication token."""
    path = token_file()
    if path.exists():
        return path.read_text().strip()
    return None


def save_token(token: str) -> None:
    """Save the authentication token to file with `0600` permissions, also when the file already
    exists with a looser mode (see `write_private_file`)."""
    write_private_file(token_file(), token)


def get_headers() -> dict:
    """Get headers with authentication token."""
    token = get_token()
    if not token:
        logger.error(f'Not authenticated on {_environment}. Please login first.')
        if TOKEN_FILE_BASE.exists():
            logger.warning(
                f'{TOKEN_FILE_BASE} is from an older version and no longer used: '
                'login again, then delete it.'
            )
        raise typer.Exit(1)
    return {'Authorization': f'Bearer {token}'}


def _password_from_prompt_or_env(password: str | None, env_var: str = 'RECEIPTS_PASSWORD') -> str:
    """A password argument that defaults to `None` (rather than being required positionally) is
    entered with the terminal's echo hidden when omitted -- typing it in the clear as a plain CLI
    argument leaves it sitting in the shell's history and briefly visible to anyone on the same
    machine running `ps`. `env_var` keeps non-interactive use (scripts, CI) working without either
    of those: `login` checks it before falling back to an interactive prompt."""
    if password is not None:
        return password
    from_env = os.environ.get(env_var)
    if from_env:
        return from_env
    return typer.prompt('Password', hide_input=True)


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
        _api_url('/receipts/uploads'),
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
        _api_url(f'/receipts/uploads/{ticket["receipt_id"]}/complete'),
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
    _select_environment(environment)
    password = _password_from_prompt_or_env(password)

    response = _request(
        'post', _api_url('/auth/login'), json={'email': email, 'password': password}
    )
    _raise_for_api_error(response, hints=False)
    try:
        save_token(response.json()['access_token'])
    except OSError as e:
        logger.error(f'Could not save the token to {token_file()}: {e}')
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
    _select_environment(environment)
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

    response = _request('get', _api_url('/receipts/receipts'), params=params, headers=get_headers())
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
    _select_environment(environment)
    headers = get_headers()

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
    _select_environment(environment)

    response = _request('get', _api_url(f'/receipts/receipts/{receipt_id}'), headers=get_headers())
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
    _select_environment(environment)
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

    headers = get_headers()
    if not out.parent.is_dir():
        # Checked before the request: an export spends one of the few per-hour exports.
        logger.error(f'Not a folder: {out.parent}')
        raise typer.Exit(1)

    import requests

    response = _request(
        'get', _api_url('/receipts/export.csv'), params=params, headers=headers, stream=True
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
    return _api_url(f'/receipts/vendors/{vendor_id}')


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
    _select_environment(environment)
    params: dict = {'status': 'to_vet' if to_vet else 'all', 'limit': limit, 'offset': offset}
    if q:
        params['q'] = q

    response = _request('get', _api_url('/receipts/vendors'), params=params, headers=get_headers())
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
    _select_environment(environment)
    body: dict = {'name': name}
    if not_vetted:
        body['vetted'] = False

    response = _request('patch', _vendor_url(vendor_id), json=body, headers=get_headers())
    clash = _conflict(response)
    if clash is not None:
        other_id = clash['conflicting_vendor_id']
        other_name = clash.get('conflicting_vendor_name') or name
        logger.error(
            f'Not renamed: you already have a vendor called {other_name!r} (id {other_id}).'
        )
        logger.error(
            f'To combine them, run: receipts vendor-merge {_environment} {other_id} {vendor_id}'
            f' (keeps vendor {other_id}).'
        )
        logger.error(
            f'Then, to use the new spelling: receipts vendor-rename {_environment} {other_id} '
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
    _select_environment(environment)
    headers = get_headers()

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
    _select_environment(environment)
    headers = get_headers()
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
        _api_url('/receipts/vendors/merge'),
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
    _select_environment(environment)

    response = _request('get', _api_url('/receipts/inbox'), headers=get_headers())
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
    _select_environment(environment)
    headers = get_headers()
    if not yes and not typer.confirm(
        'The current address stops working immediately. Generate a new one?'
    ):
        logger.info('Cancelled.')
        raise typer.Exit(1)

    response = _request('post', _api_url('/receipts/inbox/regenerate'), headers=headers)
    _raise_for_api_error(response)
    logger.info(f'New address: {response.json()["address"]}')


if __name__ == '__main__':
    app()
