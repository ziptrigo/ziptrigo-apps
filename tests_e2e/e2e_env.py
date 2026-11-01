"""How the AI-agent test server (`tests_e2e/ai/serve.py`) is configured: Django-free, so the
launcher, the preflight script and the settings module can all import it.

Two modes:

- **real** (the default of `serve.py`): the local services the site really uses -- Postgres in its
  docker container (`docker-compose.postgres.yml`), S3 on Floci (`docker-compose.floci.yml`) and
  email over SMTP, read back over IMAP, with the credentials in `tests_e2e/ai/emails.yaml`.
- **fakes** (`serve.py --fakes`, and always for the Playwright suite): a SQLite file, in-memory
  buckets and an in-memory mailbox, all inside the server process.

Everything here only reads files and environment variables. The settings module reads the
`E2E_*` variables that `server_env` sets; nothing outside `tests_e2e/` looks at them.
"""

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
MAIL_CONFIG = ROOT / 'tests_e2e' / 'ai' / 'emails.yaml'
MAIL_CONFIG_EXAMPLE = ROOT / 'tests_e2e' / 'ai' / 'emails.example.yaml'
#: Where `serve.py` records the environment of each running server (gitignored), for
#: `tests_e2e/ai/manage.py` and `preflight.py` to run against the same database, bucket and mail.
STATE_DIR = ROOT / 'tests_e2e' / 'ai' / '.run'

DEFAULT_PORT = 8765
#: The Postgres of `docker-compose.postgres.yml`, as its superuser: the test server creates and
#: drops its own database there. Override with `E2E_POSTGRES_URL`.
DEFAULT_POSTGRES_URL = 'postgres://postgres:postgres@127.0.0.1:5432/postgres'
#: Floci (`docker-compose.floci.yml`). Any key pair works there.
DEFAULT_S3_ENDPOINT = 'http://localhost:4566'
S3_ACCESS_KEY = 'test'
S3_REGION = 'us-east-1'

LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}
#: The test server only ever drops, empties or writes a database or bucket whose name starts with
#: this, so a mistyped URL can't touch the developer's own data.
NAME_PREFIX = 'ziptrigo_e2e'
BUCKET_PREFIX = 'ziptrigo-e2e'


def suffix_for(port: int) -> str:
    return '' if port == DEFAULT_PORT else f'-{port}'


def database_url(port: int) -> str:
    """The test server's own database on the Postgres server of `E2E_POSTGRES_URL`."""
    admin = urlsplit(admin_database_url())
    name = f'{NAME_PREFIX}_ai{suffix_for(port).replace("-", "_")}'
    return urlunsplit((admin.scheme, admin.netloc, f'/{name}', admin.query, ''))


def admin_database_url() -> str:
    return os.environ.get('E2E_POSTGRES_URL') or DEFAULT_POSTGRES_URL


def bucket_name(port: int) -> str:
    return f'{BUCKET_PREFIX}-ai{suffix_for(port)}'


def is_local_url(url: str) -> bool:
    return (urlsplit(url).hostname or '') in LOCAL_HOSTS


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    security: str  # starttls | ssl | none
    username: str
    password: str
    sender: str


@dataclass(frozen=True)
class ImapConfig:
    host: str
    port: int
    security: str  # ssl | starttls | none
    username: str
    password: str
    address: str
    folders: tuple[str, ...]


@dataclass(frozen=True)
class MailConfig:
    smtp: SmtpConfig
    imap: ImapConfig


class MailConfigError(Exception):
    pass


def load_mail_config(path: Path | None = None) -> MailConfig:
    """Read `emails.yaml` (`E2E_MAIL_CONFIG` names another file). Raises `MailConfigError` with
    what is missing, never with a password in the message."""
    import yaml  # dev dependency; imported here so the module stays importable without it

    path = path or Path(os.environ.get('E2E_MAIL_CONFIG') or MAIL_CONFIG)
    if not path.exists():
        raise MailConfigError(
            f'{path} does not exist. Copy {MAIL_CONFIG_EXAMPLE.name} next to it and fill in the '
            'SMTP and IMAP credentials of a mailbox the tests may use.'
        )
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except yaml.YAMLError as exc:
        raise MailConfigError(f'{path} is not valid YAML: {exc}') from None

    def section(name: str) -> dict:
        value = data.get(name)
        if not isinstance(value, dict):
            raise MailConfigError(f'{path}: the "{name}" section is missing.')
        return value

    def field(values: dict, section_name: str, key: str, default: object = None) -> str:
        value = values.get(key, default)
        if value is None or str(value).strip() == '':
            raise MailConfigError(f'{path}: "{section_name}.{key}" is missing.')
        return str(value).strip()

    def security(values: dict, section_name: str, default: str) -> str:
        value = field(values, section_name, 'security', default).lower()
        if value not in {'ssl', 'starttls', 'none'}:
            raise MailConfigError(
                f'{path}: "{section_name}.security" must be ssl, starttls or none.'
            )
        return value

    smtp = section('smtp')
    imap = section('imap')
    folders = imap.get('folders') or ['INBOX']
    if isinstance(folders, str):
        folders = [folders]
    smtp_user = field(smtp, 'smtp', 'username')
    imap_user = field(imap, 'imap', 'username')
    return MailConfig(
        smtp=SmtpConfig(
            host=field(smtp, 'smtp', 'host'),
            port=int(field(smtp, 'smtp', 'port', 587)),
            security=security(smtp, 'smtp', 'starttls'),
            username=smtp_user,
            password=field(smtp, 'smtp', 'password'),
            sender=field(smtp, 'smtp', 'from', smtp_user),
        ),
        imap=ImapConfig(
            host=field(imap, 'imap', 'host'),
            port=int(field(imap, 'imap', 'port', 993)),
            security=security(imap, 'imap', 'ssl'),
            username=imap_user,
            password=field(imap, 'imap', 'password'),
            address=field(imap, 'imap', 'address', imap_user),
            folders=tuple(str(f) for f in folders),
        ),
    )


def server_env(port: int, host: str, *, fakes: bool, run_id: str | None = None) -> dict[str, str]:
    """The environment of the test server (and of any `manage.py` command run against it)."""
    env = os.environ.copy()
    env.setdefault('ENVIRONMENT', 'local')
    env.update(
        DJANGO_SETTINGS_MODULE='tests_e2e.receipts_e2e_settings',
        BASE_URL=f'http://{host}:{port}',
        E2E_SERVICES='fakes' if fakes else 'real',
    )
    if fakes:
        env['E2E_DB_NAME'] = str(ROOT / f'db.e2e-ai{suffix_for(port)}.sqlite3')
    else:
        env.update(
            E2E_DATABASE_URL=database_url(port),
            E2E_S3_ENDPOINT_URL=os.environ.get('E2E_S3_ENDPOINT_URL') or DEFAULT_S3_ENDPOINT,
            E2E_S3_BUCKET=bucket_name(port),
            E2E_RUN_ID=run_id or env.get('E2E_RUN_ID') or secrets.token_hex(6),
        )
    return env


#: The variables a `manage.py` command needs to act on a running server's data.
STATE_KEYS = ('ENVIRONMENT', 'DJANGO_SETTINGS_MODULE', 'BASE_URL')


def write_state(port: int, env: dict[str, str]) -> Path:
    STATE_DIR.mkdir(exist_ok=True)
    state = {k: v for k, v in env.items() if k in STATE_KEYS or k.startswith('E2E_')}
    path = STATE_DIR / f'{port}.json'
    path.write_text(json.dumps(state, indent=2), encoding='utf-8')
    return path


def read_state(port: int) -> dict[str, str]:
    """The environment `serve.py --port <port>` started with. Raises `FileNotFoundError` when no
    server was started on that port."""
    path = STATE_DIR / f'{port}.json'
    return {**os.environ, **json.loads(path.read_text(encoding='utf-8'))}
