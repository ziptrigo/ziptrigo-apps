"""
Django settings for the ZipTrigo site.

One project hosts every app: the shared ones (`core`, `accounts`, `billing`) and the products
(`qr_code`, `file_transfer`, ...). See `CLAUDE.md` for how the apps are allowed to depend on each
other.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/6.0/ref/settings/
"""

import os
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

from .environment import select_env
from .secret_checks import insecure_secrets

# Build paths inside the project like this: PROJECT_ROOT / 'subdir'.
# PROJECT_ROOT, aka BASE_DIR.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Load environment variables from the selected `.env.<ENVIRONMENT>` file.
# This must happen before reading any `os.getenv(...)` values.

# Test runs may have multiple env files in the repo; default to dev. (`ty`, which replaced mypy for
# static analysis, is a standalone binary that never imports this module, so there's no equivalent
# sentinel to check for it.)
_RUNNING_TOOLING = 'pytest' in sys.modules
if _RUNNING_TOOLING:
    os.environ.setdefault('ENVIRONMENT', 'dev')

_selection = select_env(PROJECT_ROOT)

# Fail fast if env selection is broken; otherwise we'd silently read wrong defaults. Tests and
# static analysis must not require machine-local configuration, so they fall back to the defaults
# baked into this module.
if _selection.errors and not _RUNNING_TOOLING:
    raise RuntimeError('\n'.join(_selection.errors))

if _selection.environment:
    os.environ.setdefault('ENVIRONMENT', _selection.environment.value)
if _selection.env_path:
    load_dotenv(dotenv_path=_selection.env_path)

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv(
    'SECRET_KEY', 'django-insecure-8eho-(3@jki^spuj0q%+k!m9a@%d82mqy+fxe65w)6jr_e=ld2'
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG', 'False').lower() in ['true', '1']

ALLOWED_HOSTS: list[str] = [
    h.strip() for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()
]


# Application definition
# Order matters for template and static lookups: `core` comes first so its `admin/` overrides win.
INSTALLED_APPS = [
    'apps.core',
    'jazzmin',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Shared services
    'apps.accounts',
    'apps.billing',
    # Products
    'apps.qr_code',
    'apps.file_transfer',
    # Task queue: an ORM-backed store for `django.tasks` (Django 6's built-in task queue API).
    'django_tasks_db',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.core.context_processors.products',
                'apps.billing.context_processors.credits',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

# `DATABASE_URL` (e.g. `postgres://user:pass@host:5432/name`) when set; SQLite in the project root
# otherwise, for local development and tests. Production must set it (checked below): inside the
# container, `db.sqlite3` would be lost on every redeploy.
DATABASE_URL = os.getenv('DATABASE_URL', '')

if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(DATABASE_URL, conn_max_age=600, conn_health_checks=True)
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': PROJECT_ROOT / 'db.sqlite3',
        }
    }

if os.getenv('ENVIRONMENT') == 'prod' and not DATABASE_URL:
    raise ImproperlyConfigured(
        'Set DATABASE_URL in production; the SQLite fallback would be lost on every redeploy.'
    )


# Cache: general-purpose caching on `'default'`, plus (only when `CACHE_URL` is set) a dedicated
# `'ratelimit'` alias `apps.core.ratelimit` can use instead of its DB-backed default -- see
# `RATELIMIT_STORAGE` below.
# - Tests (`_RUNNING_TOOLING`): `LocMemCache` on both aliases -- no shared service to depend on,
#   and each test process gets its own isolated dict. Rate limiting defaults to disabled under
#   pytest anyway (see `RATELIMIT_ENABLE` below); tests that enable it either exercise the DB
#   storage path directly (`RATELIMIT_STORAGE` also defaults to `'db'` under pytest, same as
#   dev/prod without `CACHE_URL` -- "runs on the test DB naturally") or opt into this `'ratelimit'`
#   LocMem alias with `override_settings(RATELIMIT_STORAGE='cache')` to exercise that path's logic
#   instead (see `apps/core/tests/test_ratelimit.py`).
# - `CACHE_URL` set (any environment): `RedisCache` on both aliases, and `RATELIMIT_STORAGE`
#   becomes `'cache'` -- for when there's a shared Redis worth pointing rate limiting (or the
#   site's general cache) at. A full cache URL, e.g. `redis://host:6379/0`; requires the `redis`
#   package to actually be installed at runtime (not a hard dependency of this project, since it's
#   opt-in).
# - Otherwise (dev/prod default): just `'default'` (`LocMemCache`, per-process -- fine for the
#   site's own incidental caching needs). No `'ratelimit'` alias at all: `RATELIMIT_STORAGE` is
#   `'db'`, which doesn't use a cache backend (see `apps.core.ratelimit.limiter`'s docstring for
#   why not `DatabaseCache`, which this replaced -- issue #53 code review).
CACHE_URL = os.getenv('CACHE_URL', '')

if _RUNNING_TOOLING:
    CACHES = {
        'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
        'ratelimit': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
    }
elif CACHE_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': CACHE_URL,
        },
        'ratelimit': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': CACHE_URL,
        },
    }
else:
    CACHES = {
        'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
    }


# Rate limiting (issue #53): `apps.core.ratelimit` enforces every entry in `RATELIMIT_RULES`.
# `RATELIMIT_ENABLE` is the project-wide kill switch -- off by default under pytest so unrelated
# tests can't become flaky by incidentally tripping a limit; tests that specifically exercise a
# limit turn it on with the `settings`/`override_settings` fixture.
RATELIMIT_ENABLE = os.getenv(
    'RATELIMIT_ENABLE', 'False' if _RUNNING_TOOLING else 'True'
).lower() in ('true', '1')

# Which storage backend `apps.core.ratelimit` counts hits against (issue #53 code review):
# `'cache'` (the `'ratelimit'` alias above -- `RedisCache` in practice, since that's the only case
# that sets `CACHE_URL`) when one's configured, `'db'` otherwise (`apps.core.models.RateLimitCounter`,
# an atomic upsert against whichever database `DATABASES['default']` already is -- Postgres in
# prod, SQLite under pytest). `'db'` is also what dev/prod get with no `CACHE_URL`, and what pytest
# gets by default with no `CACHE_URL` set for test runs either -- see `apps/core/ratelimit/limiter.py`'s
# docstring for both backends' semantics and why `DatabaseCache` (this setting's predecessor) was
# replaced rather than fixed in place.
RATELIMIT_STORAGE = 'cache' if CACHE_URL else 'db'

# Trusted proxies for `apps.core.services.client_ip.client_ip` (issue #53 code review): a
# comma-separated list of IPs and/or CIDRs. `X-Real-IP` is only honoured when `REMOTE_ADDR` (the
# actual TCP peer, which a client can't spoof) matches one of these -- otherwise `REMOTE_ADDR`
# itself is used, so a client that can reach this app directly can never forge its own IP for
# rate limiting or `DownloadEvent.ip`/`Transfer.sender_ip` just by setting the header. The default
# covers loopback + RFC 1918 + unique-local IPv6 (`fc00::/7`): this project's own nginx reaches
# gunicorn over a private Docker bridge address, never a public one -- **gunicorn itself must only
# ever be published on an internal interface in production** (never bound to a public one
# directly), since anything that can open a TCP connection straight to gunicorn can set
# `X-Real-IP` to whatever it likes and this setting is the only thing standing between that and a
# forged client IP.
_DEFAULT_TRUSTED_PROXIES = '127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7'
TRUSTED_PROXIES = [
    entry.strip()
    for entry in os.getenv('TRUSTED_PROXIES', _DEFAULT_TRUSTED_PROXIES).split(',')
    if entry.strip()
]

# name -> (max hits, window in seconds). Every limit is deliberately generous headroom against
# abuse (scripted brute force, storage/SES cost, CPU-bound rendering), not a precise per-user
# quota -- see `apps/core/ratelimit/limiter.py` for the counting scheme this reads into.
#
# Keyed per client IP (`apps.core.services.client_ip`) unless noted; "per account"/"per email"
# rules run *in addition to* the matching per-IP rule, not instead of it, so a single victim
# account/address can't be hammered from many IPs, and a single IP can't be used to hammer many
# accounts/addresses, without either rule alone having to be uncomfortably strict.
RATELIMIT_RULES: dict[str, tuple[int, int]] = {
    # -- accounts: login, signup, password reset, email confirmation --
    # Login (issue #53 code review: no rule here is a lockout -- see
    # `apps.accounts.services.login_throttle`'s module docstring for the full reasoning):
    # - `LOGIN_IP`: per-IP, throttles scripted credential stuffing regardless of which account(s)
    #   it targets.
    # - `LOGIN_ACCOUNT_IP`: strict, per submitted-email-*and*-IP, counts every attempt regardless
    #   of outcome. Throttles one attacking IP hammering one target account hard, without needing
    #   to know whether the password was right, and -- because it's scoped to *that* IP -- never
    #   affects the real owner's own login from their own IP.
    # - `LOGIN_ACCOUNT`: looser, per submitted email alone, but counts only *failed* attempts
    #   (checked with `peek` before `authenticate()`, only recorded once `authenticate()` actually
    #   returns `None`). A backstop against the same account being brute-forced from many
    #   different IPs (each with its own `LOGIN_ACCOUNT_IP` budget) -- and, because only failures
    #   count, submitting a victim's email with *wrong* passwords can never by itself lock the
    #   real owner out of their own, correct one.
    # Shared between the session view (`POST /account/login/`) and the JWT endpoint
    # (`POST /api/auth/login`) -- same keys, same budgets, so one surface can't double the other's
    # allowance.
    'LOGIN_IP': (20, 5 * 60),
    'LOGIN_ACCOUNT_IP': (10, 5 * 60),
    'LOGIN_ACCOUNT': (30, 15 * 60),
    # Signup sends a confirmation email (SES cost); no per-account rule makes sense pre-signup.
    'SIGNUP_IP': (5, 60 * 60),
    # Forgot-password: the response is identical whether or not the account exists either way, so
    # a 429 here reveals nothing a normal response wouldn't already hide (CLAUDE.md: must not leak
    # existence) -- but there's no "authenticate()" here to gate a failed-only counter on (every
    # submission has the same, single outcome), so the per-email protection is a flat cap instead,
    # same idea as login's split: `FORGOT_PASSWORD_EMAIL_IP` is the strict per-(email, IP) budget,
    # `FORGOT_PASSWORD_EMAIL` a looser one across every IP -- a third party who merely knows a
    # victim's address needs many different IPs to exhaust the looser cap and actually block their
    # reset, rather than the handful of same-IP requests the old single per-email rule allowed.
    'FORGOT_PASSWORD_IP': (10, 60 * 60),
    'FORGOT_PASSWORD_EMAIL_IP': (3, 60 * 60),
    'FORGOT_PASSWORD_EMAIL': (15, 60 * 60),
    # Resend-confirmation: per-IP here; the per-*email*-per-day cap is enforced once, centrally,
    # by `EMAIL_VERIFICATION_START_EMAIL` below (every caller of
    # `apps.core.services.email_verification.start` shares it, including this endpoint).
    'RESEND_CONFIRMATION_IP': (10, 60 * 60),
    # -- core: shared email verification (`apps.core.services.email_verification.start`) --
    # The per-email-address-per-day cap on verification *starts* (CLAUDE.md Known gaps: a resend
    # every 60s cooldown alone still allows ~1,440 sends/day, each with fresh guess attempts).
    # Applies within each caller-supplied `rate_limit_group` (`start`'s parameter -- default the
    # `purpose`), across every purpose *within* that group -- so `accounts`' account-lifecycle
    # emails and `file_transfer`'s anonymous-send emails (a single shared group across every
    # transfer's own per-transfer purpose) draw from separate budgets and can't starve each other,
    # while still closing the cross-transfer version of the gap within file_transfer's own group
    # (issue #53 code review; see `start`'s docstring).
    'EMAIL_VERIFICATION_START_EMAIL': (20, 24 * 60 * 60),
    # -- qr_code: preview (CPU-bound rendering) and create (writes to media) --
    # Both require login on every surface (web + `/api/qr/`), so keyed per user rather than IP.
    'QR_PREVIEW_USER': (30, 60),
    'QR_CREATE_USER': (20, 60),
    # `/go/<code>` short-link redirects: public, high-traffic by design (that's the point of a QR
    # code), so deliberately generous. See `apps/qr_code/views/redirect.py` for what happens when
    # this is exceeded: redirect anyway (a real visitor behind a busy shared IP/NAT must never see
    # an error just because someone else scanned the same code), but skip the scan-count write --
    # this rule exists to protect `QRCode.scan_count`'s *accuracy* under that kind of shared-IP
    # burst, not to shed load (an increment is already one cheap `UPDATE`; the redirect itself is
    # the expensive-if-anything part, and that never gets skipped).
    'QR_REDIRECT_IP': (120, 60),
    # -- file_transfer: anonymous sending (issue #55 phase 2) --
    'FT_ANON_UPLOAD_IP': (90, 60),
    'FT_ANON_CONFIRM_START_IP': (10, 60 * 60),
    'FT_ANON_CONFIRM_RESEND_IP': (10, 60 * 60),
    'FT_ANON_CONFIRM_CODE_IP': (20, 10 * 60),
    'FT_ANON_CONFIRM_LINK_IP': (30, 10 * 60),
    # -- file_transfer: logged-in sending --
    # Generous: a real multi-file upload legitimately calls this often in a short span.
    'FT_UPLOAD_USER': (120, 60),
    # -- file_transfer: public download page (`/t/<slug>/`) --
    'FT_DOWNLOAD_IP': (120, 60),
    # Password attempts (issue #53 code review, same split as login): `FT_UNLOCK_IP` stays a
    # strict, every-attempt-counts per-IP limit; `FT_UNLOCK_TRANSFER` becomes a looser per-transfer
    # ceiling that counts only *wrong* passwords, so a third party who merely knows (or guesses) a
    # transfer's slug can no longer lock the real recipient out of a transfer whose password they
    # actually have.
    'FT_UNLOCK_IP': (15, 10 * 60),
    'FT_UNLOCK_TRANSFER': (30, 10 * 60),
    # Anonymous sender's manage link (`/t/<slug>/manage/<token>/`): low legitimate traffic.
    'FT_MANAGE_IP': (30, 60 * 60),
    # -- file_transfer: abuse reports (issue #59) --
    # `FT_REPORT_IP`: strict, every submission counts -- a real visitor reports at most a
    # handful of transfers, ever. `FT_REPORT_TRANSFER`: looser, per-transfer ceiling so many
    # different IPs genuinely reporting the same abusive transfer don't get throttled by the IP
    # rule alone, while still bounding how many reports (and so admin noise / auto-hold triggers)
    # one transfer can accumulate per hour.
    'FT_REPORT_IP': (5, 60 * 60),
    'FT_REPORT_TRANSFER': (20, 60 * 60),
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 6,
        },
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# Default primary key field type
# https://docs.djangoproject.com/en/6.0/ref/settings/#default-auto-field
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/
# Every app keeps its static files namespaced under `<app>/static/<app>/`.
STATIC_URL = '/static/'
STATIC_ROOT = PROJECT_ROOT / 'staticfiles'

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = PROJECT_ROOT / 'media'


# Authentication
AUTH_USER_MODEL = 'accounts.User'

AUTHENTICATION_BACKENDS = [
    'apps.accounts.backends.EmailBackend',
]

# Login URL for @login_required decorator
LOGIN_URL = 'accounts:login'

# Base URL used to build absolute links (emails, QR code redirects).
BASE_URL = os.getenv('BASE_URL', 'http://localhost:8000')

# Deployed behind nginx, which terminates TLS and always sets `X-Forwarded-Proto`. Without this,
# Django sees every request as plain HTTP, and the CSRF check rejects HTTPS form posts because their
# `Origin` (`https://...`) doesn't match. The site's own origin is trusted explicitly as well.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
_base_url = urlsplit(BASE_URL)
CSRF_TRUSTED_ORIGINS = [f'{_base_url.scheme}://{_base_url.netloc}']

# Only send the session and CSRF cookies over HTTPS when the site is served over HTTPS.
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = _base_url.scheme == 'https'

# Token lifetimes (hours). Email confirmation moved onto `apps.core.services.email_verification`
# (issue #58), which otherwise defaults to `CoreSettings.email_verification_validity_minutes` --
# this is passed as `start()`'s per-call `validity` override instead, to keep signup
# confirmation's historical lifetime unchanged (behaviour for existing users must not change).
EMAIL_CONFIRMATION_TOKEN_TTL_HOURS = int(os.getenv('EMAIL_CONFIRMATION_TOKEN_TTL_HOURS', '48'))
PASSWORD_RESET_TOKEN_TTL_HOURS = int(os.getenv('PASSWORD_RESET_TOKEN_TTL_HOURS', '4'))

# JWT signing for the `/api/` endpoints. Separate from `SECRET_KEY` so it can be rotated on its own.
JWT_SECRET = os.getenv('JWT_SECRET', 'change-me-in-production')
JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')
JWT_EXP_DELTA_SECONDS = int(os.getenv('JWT_EXP_DELTA_SECONDS', str(14 * 24 * 3600)))

# Production must not run on the development fallbacks above: anyone could forge sessions and
# JWTs signed with them.
if os.getenv('ENVIRONMENT') == 'prod':
    _insecure = insecure_secrets({'SECRET_KEY': SECRET_KEY, 'JWT_SECRET': JWT_SECRET})
    if _insecure:
        raise ImproperlyConfigured(
            f'Set real values for {", ".join(_insecure)} in production; '
            'they are missing or still placeholders.'
        )

# django-ninja-jwt settings
NINJA_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(seconds=JWT_EXP_DELTA_SECONDS),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
    'ROTATE_REFRESH_TOKENS': False,
    'BLACKLIST_AFTER_ROTATION': False,
    'UPDATE_LAST_LOGIN': False,
    'ALGORITHM': JWT_ALGORITHM,
    'SIGNING_KEY': JWT_SECRET,
    'VERIFYING_KEY': None,
    'AUDIENCE': None,
    'ISSUER': None,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'sub',
    'USER_AUTHENTICATION_RULE': 'ninja_jwt.authentication.default_user_authentication_rule',
    'AUTH_TOKEN_CLASSES': ('apps.accounts.tokens.CustomAccessToken',),
    'TOKEN_TYPE_CLAIM': 'token_type',
    'JTI_CLAIM': 'jti',
    'SLIDING_TOKEN_REFRESH_EXP_CLAIM': 'refresh_exp',
    'SLIDING_TOKEN_LIFETIME': timedelta(minutes=5),
    'SLIDING_TOKEN_REFRESH_LIFETIME': timedelta(days=1),
}


# Email settings
# Comma-separated list of email backends to use. Example: "console" or "ses,console".
EMAIL_BACKENDS = os.getenv('EMAIL_BACKENDS', '')

# Tests should not require external configuration.
if not EMAIL_BACKENDS and _RUNNING_TOOLING:
    EMAIL_BACKENDS = 'console'

AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
AWS_SES_SENDER = os.getenv('AWS_SES_SENDER', 'no-reply@ziptrigo.com')


# File transfer storage: a private S3 bucket per environment, with its own IAM user (both in the
# `infra` repo, `apps/ziptrigo-apps`). Separate from the SES credentials above because presigned
# URLs need long-lived keys, not the temporary ones from assuming `AWS_ROLE`. In dev, point
# `FILE_TRANSFER_S3_ENDPOINT_URL` at Floci (`docker-compose.floci.yml`).
FILE_TRANSFER_S3_BUCKET = os.getenv('FILE_TRANSFER_S3_BUCKET', '')
FILE_TRANSFER_S3_REGION = os.getenv('FILE_TRANSFER_S3_REGION', AWS_REGION)
FILE_TRANSFER_S3_ENDPOINT_URL = os.getenv('FILE_TRANSFER_S3_ENDPOINT_URL') or None
FILE_TRANSFER_AWS_ACCESS_KEY_ID = os.getenv('FILE_TRANSFER_AWS_ACCESS_KEY_ID', '')
FILE_TRANSFER_AWS_SECRET_ACCESS_KEY = os.getenv('FILE_TRANSFER_AWS_SECRET_ACCESS_KEY', '')


# Like `SECRET_KEY` and `JWT_SECRET` above: production must not start without real storage
# credentials, or every upload would fail.
if os.getenv('ENVIRONMENT') == 'prod':
    _insecure = insecure_secrets(
        {
            'FILE_TRANSFER_AWS_ACCESS_KEY_ID': FILE_TRANSFER_AWS_ACCESS_KEY_ID,
            'FILE_TRANSFER_AWS_SECRET_ACCESS_KEY': FILE_TRANSFER_AWS_SECRET_ACCESS_KEY,
        }
    )
    if _insecure:
        raise ImproperlyConfigured(
            f'Set real values for {", ".join(_insecure)} in production; '
            'they are missing or still placeholders.'
        )


# Task queue: Django 6's built-in `django.tasks`, backed by `django_tasks_db` (an ORM-based
# backend; Django core only ships the Immediate/Dummy backends). One `worker` container runs
# `./manage.py db_worker` to execute queued tasks (emails, zip building, deleting a transfer's
# objects). Tests run everything inline via `ImmediateBackend` so they don't depend on a worker.
TASKS = {
    'default': {
        'BACKEND': (
            'django.tasks.backends.immediate.ImmediateBackend'
            if _RUNNING_TOOLING
            else 'django_tasks_db.DatabaseBackend'
        ),
        'QUEUES': ['default'],
    }
}

# apps.core.scheduler: how often (seconds) a scheduler runner -- the `run_scheduler` management
# command's, or the thread `start_scheduler_thread()` starts inside each gunicorn worker -- checks
# for due jobs. Each job's own interval (in `JobSpec`) controls how often it actually runs.
SCHEDULER_TICK_SECONDS = int(os.getenv('SCHEDULER_TICK_SECONDS', '30'))

# Start `apps.core.scheduler.start_scheduler_thread()` inside every gunicorn worker (see
# `gunicorn.conf.py`). Off by default so a local checkout, `runserver` and pytest never run jobs.
SCHEDULER_ENABLED = os.getenv('SCHEDULER_ENABLED', 'False').lower() in ('true', '1')

# Python's default logging drops INFO from our own loggers; the scheduler's start/run lines
# ("Scheduler thread started in pid ...", "Scheduler ran: ...") need to reach the container logs.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'loggers': {'apps.core.scheduler': {'handlers': ['console'], 'level': 'INFO'}},
}


# QR code settings
QR_CODE_REDIRECT_PATH = '/go/'


# Jazzmin configuration
JAZZMIN_SETTINGS = {
    'site_title': 'ZipTrigo Admin',
    'site_header': 'ZipTrigo Administration',
    'site_brand': 'ZipTrigo',
    'welcome_sign': 'ZipTrigo Admin',
    'copyright': 'ZipTrigo',
    'search_model': ['accounts.User'],
    'show_sidebar': True,
    'navigation_expanded': True,
    'theme': 'default',
    'dark_mode_theme': 'darkly',
    'default_icon_parents': 'fas fa-chevron-right',
    'default_icon_children': 'fas fa-arrow-right',
    'related_modal_active': True,
    'custom_css': 'core/css/jazzmin_custom.css',
    'custom_js': 'core/js/admin_theme_toggle.js',
    'user_avatar': None,
    'login_logo': 'core/images/logo_128x128.png',
    'site_logo': 'core/images/logo_128x128.png',
    'icons': {
        'accounts.User': 'fas fa-user-circle',
        'billing.CreditTransaction': 'fas fa-coins',
        'billing.CreditAccount': 'fas fa-wallet',
        'qr_code.QRCode': 'fas fa-qrcode',
        'core.ScheduledJob': 'fas fa-clock',
        'file_transfer.Transfer': 'fas fa-paper-plane',
        'file_transfer.DownloadEvent': 'fas fa-download',
        'file_transfer.FileTransferSettings': 'fas fa-sliders-h',
    },
    'topmenu_links': [
        {'name': 'Site', 'url': 'core:home'},
        {
            'name': 'Admin Tools',
            'url': 'custom_admin:admin_tools',
            'permissions': ['accounts.add_user'],
        },
    ],
}
