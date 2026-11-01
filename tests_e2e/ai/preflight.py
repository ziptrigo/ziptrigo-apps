"""Check that the AI-agent test server's services work before a test plan runs.

    python tests_e2e/ai/preflight.py [--port 8765] [--no-mail]

Runs against the environment `serve.py --port <port>` recorded when it started (so start the
server first; `serve.py` itself runs this before it serves) and checks, in real-services mode:

- **Database**: the Postgres e2e database answers, is local, has every migration and the seeded
  accounts, and takes writes;
- **S3 (Floci)**, for each product's storage (file transfer, Receipts, To-do): the bucket is the e2e
  one on a local endpoint, objects can be written, read and deleted under the product's prefix, the
  CORS rule lets the site's origin upload from a browser (and exposes `ETag`), a presigned PUT
  works and refuses a wrong checksum, and a presigned GET returns the bytes;
- **Email**: `emails.yaml` is complete, SMTP and IMAP sign in, and a probe email sent over SMTP
  is read back over IMAP with its link intact.

In `--fakes` mode it only checks the SQLite database and that the in-memory fakes are installed.
Exits 1 when any check fails. The same checks are at `/__e2e__/preflight/` on the running server.

`serve.py` also passes `--prepare-bucket` (create the bucket and set its CORS rule),
`--empty-bucket` and `--purge-old-mail`.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests_e2e import e2e_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=e2e_env.DEFAULT_PORT)
    parser.add_argument('--no-mail', action='store_true', help='skip the SMTP/IMAP round trip')
    parser.add_argument('--prepare-bucket', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--empty-bucket', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--purge-old-mail', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()

    try:
        os.environ.update(e2e_env.read_state(args.port))
    except FileNotFoundError:
        print(f'No test server was started on port {args.port}: run serve.py first.')
        return 1
    os.chdir(ROOT)

    import django

    django.setup()

    from django.conf import settings

    from tests_e2e import real_services

    real = settings.E2E_SERVICES == 'real'
    if real and args.prepare_bucket:
        real_services.prepare_bucket(empty=args.empty_bucket)
    if real and args.purge_old_mail:
        deleted = real_services.mailbox().purge_other_runs()
        print(f'Deleted {deleted} emails of earlier runs from the IMAP mailbox.')

    print(f'Preflight ({"real services" if real else "fakes"}), {settings.BASE_URL}:')
    checks = real_services.run_checks(real=real, mail=not args.no_mail)
    width = max(len(f'{c.area}: {c.name}') for c in checks)
    for check in checks:
        label = f'{check.area}: {check.name}'
        print(f'  {"PASS" if check.ok else "FAIL"}  {label:<{width}}  {check.detail}')
    failed = [c for c in checks if not c.ok]
    print(f'{len(checks) - len(failed)} passed, {len(failed)} failed.', flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
