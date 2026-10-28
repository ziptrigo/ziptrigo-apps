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

# Test runs may have multiple env files in the repo; default to local. (`ty`, which replaced mypy for
# static analysis, is a standalone binary that never imports this module, so there's no equivalent
# sentinel to check for it.)
_RUNNING_TOOLING = 'pytest' in sys.modules
if _RUNNING_TOOLING:
    os.environ.setdefault('ENVIRONMENT', 'local')

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
    'apps.feedback',
    # Products
    'apps.qr_code',
    'apps.file_transfer',
    'apps.receipts',
    'apps.short_url',
    'apps.todo',
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
    # Activates the signed-in user's time zone for the request (issue #177); needs `request.user`.
    'apps.accounts.middleware.TimezoneMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
# Paths `TimezoneMiddleware` leaves alone: it would otherwise resolve `request.user` (a session and
# a user query) on the hot public ones. The short links and the download page serve visitors, not
# accounts, static files render nothing, and the API authenticates with JWTs, not sessions, so none
# of them has a saved zone to activate; they render in `TIME_ZONE` (UTC).
TIMEZONE_MIDDLEWARE_SKIP_PREFIXES = ('/go/', '/s/', '/t/', '/static/', '/api/')

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
#   local/prod without `CACHE_URL` -- "runs on the test DB naturally") or opt into this `'ratelimit'`
#   LocMem alias with `override_settings(RATELIMIT_STORAGE='cache')` to exercise that path's logic
#   instead (see `apps/core/tests/test_ratelimit.py`).
# - `CACHE_URL` set (any environment): `RedisCache` on both aliases, and `RATELIMIT_STORAGE`
#   becomes `'cache'` -- for when there's a shared Redis worth pointing rate limiting (or the
#   site's general cache) at. A full cache URL, e.g. `redis://host:6379/0`; requires the `redis`
#   package to actually be installed at runtime (not a hard dependency of this project, since it's
#   opt-in).
# - Otherwise (local/prod default): just `'default'` (`LocMemCache`, per-process -- fine for the
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
# prod, SQLite under pytest). `'db'` is also what local/prod get with no `CACHE_URL`, and what pytest
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
    # Captcha attempts on the register page (issue #117): every submission that reaches the
    # Turnstile check costs one outbound call to Cloudflare, which this bounds. Looser than
    # `SIGNUP_IP` (which only counts submissions that passed the captcha), so a person who fails
    # the check a few times still gets through.
    'SIGNUP_CAPTCHA_IP': (30, 60 * 60),
    # Signing up with an address that already has an account emails its owner (issue #125) -- a
    # "you already have an account" notice, or, for an account never confirmed, a "finish setting
    # up" link -- and the page itself answers the same as for a new address, so it can't be used to
    # find out whether an address is registered. Per (lower-cased) address across every IP: the
    # form is open to anyone with a solved captcha, so without these it would be a way to flood a
    # stranger's inbox. One an hour is plenty for the owner who forgot they had signed up, and the
    # daily ceiling stops a patient sender from getting one in every hour all day (`SIGNUP_IP`
    # bounds how fast a single IP can try). Both are checked together and counted only when an email
    # is actually queued; over either, the email is skipped silently and the response doesn't
    # change. (Locking down an unconfirmed account is not rate limited: it is what protects it.)
    'SIGNUP_EXISTING_EMAIL': (1, 60 * 60),
    'SIGNUP_EXISTING_EMAIL_DAY': (3, 24 * 60 * 60),
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
    # The "Confirm my email" button the emailed link lands on (`POST /account/confirm-email/<token>/`).
    # Only the POST costs anything (a token lookup and one conditional write); the GET that shows
    # the button is cheap and unmetered. Generous: a real person clicks once, perhaps twice, and an
    # office/CGNAT address may see a whole team confirm at once.
    'CONFIRM_EMAIL_IP': (30, 10 * 60),
    # "Sign in with Google" callback (issue #91): every real sign-in is one hit, so this is
    # headroom for a shared NAT/office IP signing in at the same time, not a per-user quota. The
    # callback does an outbound token exchange with Google per call, which is what this protects.
    'GOOGLE_LOGIN_IP': (30, 5 * 60),
    # "Sign in with Microsoft" callback (issue #156): the same reasoning and numbers as
    # `GOOGLE_LOGIN_IP` (an outbound token exchange per call, headroom for a shared NAT/office IP).
    'MICROSOFT_LOGIN_IP': (30, 5 * 60),
    # Change password on the account page: logged in, so keyed per user, and counts *every*
    # attempt before the current password is checked -- a stolen session can't brute-force the
    # password through it, even with parallel requests (a failed-only counter, checked with `peek`
    # first, lets a whole burst through before the first failure is recorded). Only the session's
    # own user can spend it, so there's no lockout of anyone else to avoid; the budget leaves room
    # for a few typos in the new password.
    'CHANGE_PASSWORD_USER': (10, 15 * 60),
    # Changing the email with `PUT /api/account`: it answers 400 "Email already in use" for an
    # address that has an account (issue #125), so every attempted *change* counts, keyed per user
    # and charged before that check -- the caller is logged in, so this bounds how many addresses
    # one account can probe, and a real change is a once-in-a-blue-moon event (a few typos at most).
    # A name-only update, or resubmitting the current address, is not a change and costs nothing.
    'ACCOUNT_EMAIL_CHANGE_USER': (5, 60 * 60),
    # The browser's automatic time zone detection (`POST /account/timezone/`, issue #177), per
    # user and charged for every call. A real client posts once per browser session and only
    # while the account has no zone, so it needs a handful at most; the rest is headroom for
    # several devices and tabs, and a bound on a script hammering the endpoint.
    'ACCOUNT_TIMEZONE_USER': (10, 60 * 60),
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
    # -- short_url (issue #162): the `/s/<code>` redirect and the owner's pages --
    # `/s/<code>` redirects: public and high-traffic by design, so as generous as `/go/<code>`.
    # Same behaviour over the limit (`apps/short_url/views/redirect.py`): charged *before* the
    # lookup, so made-up codes spend the budget too; a known link still redirects (a visitor behind
    # a busy shared IP must never see an error because others followed the same link) but its
    # click event is not recorded, and an unknown code is a 404, never a 429. The rule protects the
    # accuracy of the click counts, it doesn't shed load (nginx's looser `/s/` zone, an infra
    # follow-up, does that). One exception, deliberately unlike `/go/`: a link with a click cap
    # (`max_clicks`) is still counted over the limit, because there the counter is what ends the
    # link, not just a statistic; do not "fix" that to match.
    'SU_REDIRECT_IP': (120, 60),
    # Creating a link (web form and, later, `/api/s/`): per user. Each one is a database row
    # (and a background request to the destination page for its title), so hourly, but well above
    # what a person typing links in does.
    'SU_CREATE_USER': (30, 60 * 60),
    # Pricing a create (`POST /api/s/links/quote`) and, before its own check, a create: per user.
    # Neither writes or spends anything, but each runs a few lookups (owner, cap, alias, retired
    # codes, block list), so this bounds a client that polls or retries a refused create. Loose on
    # purpose: a refused create spends this and not `SU_CREATE_USER`, which stays the budget for
    # real links, and a person or CLI never gets near it.
    'SU_QUOTE_USER': (120, 60),
    # Editing a link (destination, title, tags, expiry, click cap): per user.
    'SU_EDIT_USER': (60, 60 * 60),
    # The QR image of a link (CPU-bound rendering): per user, shared by the dashboard and the
    # link page. Looser than creating: a page of rows can show several at once.
    'SU_QR_USER': (60, 60),
    # The password page of a protected link (`/s/<code>/unlock`), the same split as file
    # transfer's `FT_UNLOCK_*` (and for the same reason, see "Rate limiting" in CLAUDE.md):
    # `SU_PASSWORD_IP` is strict and counts every attempt; `SU_PASSWORD_LINK` is a looser ceiling
    # per link, across every IP, that counts only *wrong* passwords (peeked before the attempt,
    # hit after it fails), so someone who merely knows a code can't lock the real recipients out
    # while one password still can't be guessed from many addresses.
    'SU_PASSWORD_IP': (15, 10 * 60),
    'SU_PASSWORD_LINK': (30, 10 * 60),
    # The preview page (`/s/<code>+`): a page someone opens deliberately, so far fewer than
    # redirects; over the limit it is a plain 429 (unlike the redirect, which never errors).
    'SU_PREVIEW_IP': (60, 60),
    # Abuse reports on a link (`/s/<code>/report`), the same split as file transfer's
    # `FT_REPORT_*` and for the same reason. `SU_REPORT_IP`: strict, every submission counts, since
    # a real visitor reports a handful of links, ever. `SU_REPORT_LINK`: a looser per-link
    # ceiling, peeked before a report is tried and hit only once one was created, so many
    # different people genuinely reporting one bad link aren't throttled by the IP rule alone,
    # while the reports (admin noise, auto-hold triggers) one link can collect per hour are still
    # bounded.
    'SU_REPORT_IP': (5, 60 * 60),
    'SU_REPORT_LINK': (20, 60 * 60),
    # -- todo (issue #178): the To-do pages (and, later, `/api/todo/`) --
    # Creating a task or a subtask: per user, shared by the web and the API. Each one is a row and
    # may cost credits, so well above what a person typing tasks in does, but bounded.
    'TODO_CREATE_USER': (60, 60),
    # Every other change (edit, complete, postpone, trash, reorder, labels, settings): per user,
    # shared by the web and the API. Looser than creating: ticking off a list, or dragging, is a
    # burst of small writes.
    'TODO_EDIT_USER': (240, 60),
    # Adding and editing a comment (issue #180): per user, shared by the web and the API. Every
    # one is a row of up to 10,000 characters (an edit also a revision), so tighter than the
    # other edits; deleting and restoring a comment spend `TODO_EDIT_USER`.
    'TODO_COMMENT_USER': (60, 60),
    # Attachments (issue #184), per user, shared by the web and the API. `start` presigns an
    # upload and creates a row, so it is bounded like a real multi-file upload (a dozen files at
    # a time, retried); `complete` is a free S3 HEAD even for an object that never arrived, so it
    # has its own looser rule rather than riding on `start`'s; a download is a presign, no write.
    'TODO_UPLOAD_USER': (120, 60),
    'TODO_UPLOAD_COMPLETE_USER': (240, 60),
    'TODO_DOWNLOAD_USER': (120, 60),
    # Reading a date out of a task title with the AI (issue #183), `POST /todo/parse/`: per user,
    # charged before the model is called. The page asks after a 600 ms pause in typing, so a
    # person stays far below it; it bounds what a script can make the AI bill.
    'TODO_PARSE_USER': (60, 60),
    # -- file_transfer: anonymous sending (issue #55 phase 2) --
    'FT_ANON_UPLOAD_IP': (90, 60),
    'FT_ANON_CONFIRM_START_IP': (10, 60 * 60),
    'FT_ANON_CONFIRM_RESEND_IP': (10, 60 * 60),
    'FT_ANON_CONFIRM_CODE_IP': (20, 10 * 60),
    'FT_ANON_CONFIRM_LINK_IP': (30, 10 * 60),
    # -- file_transfer: logged-in sending --
    # Generous: a real multi-file upload legitimately calls this often in a short span.
    'FT_UPLOAD_USER': (120, 60),
    # Emails sent from an *existing* transfer, web dashboard and `/api/ft/` sharing one budget:
    # "Resend" to one recipient and "Add recipients". Both email the sender's free-text message,
    # so uncapped they turn our SES account into a spam cannon aimed at anyone (a transfer costs
    # one credit). It is charged one hit per *email* (a resend is 1, an add is one per new
    # address), after the request has passed every other check, so this is the most such emails
    # one account can send an hour: 30. The initial send is not counted (a transfer
    # costs a credit and is capped by the recipient limit). A real sender resends or adds a
    # handful of addresses per transfer. How many go to *one* address is bounded separately, by
    # the resend cooldown (`services.actions.RESEND_COOLDOWN`).
    'FT_RECIPIENT_EMAIL_USER': (30, 60 * 60),
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
    # -- feedback (issue #82) --
    # Logged-in only, so keyed per user. Every submission emails every superuser, so without a cap
    # one account could flood the admins' inboxes; a real user sends a message or two, ever.
    'FEEDBACK_USER': (10, 60 * 60),
    # -- receipts (issue #98) --
    # All five are keyed per user (every receipts surface requires login) and shared between the
    # web pages and `/api/receipts/`, so one surface can't double the other's budget. Enforced at
    # the views/API, not in the services.
    # Starting an upload is one cheap row plus a presign, but a real multi-file drop calls it once
    # per file in a burst, and each file later costs an AI call; the daily per-user file cap in
    # `ReceiptsSettings` is the actual quota, this is only headroom against a scripted flood.
    'RECEIPTS_UPLOAD_USER': (120, 60),
    # `complete` runs one S3 HEAD per call and, for an object that never arrived, changes nothing --
    # so without a cap one started upload could be completed in a loop forever. Looser than
    # `start`: a legitimate client calls it once per file (plus a retry on 429), and a big
    # multi-file drop finishes its uploads in bursts, so this only has to stop a scripted loop.
    'RECEIPTS_UPLOAD_COMPLETE_USER': (240, 60),
    # Re-running extraction spends credits and an AI call every time, so a runaway client (or a
    # stuck "Re-run" button) should hit a wall long before it drains a balance.
    'RECEIPTS_REPROCESS_USER': (30, 60 * 60),
    # A CSV export walks every matching row; a handful an hour is plenty for a human.
    'RECEIPTS_EXPORT_USER': (10, 60 * 60),
    # Regenerating retires the address and mints a new one; there is no legitimate reason to do it
    # more than a few times, and doing it in a loop only churns rows.
    'RECEIPTS_REGENERATE_USER': (5, 60 * 60),
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

# "Sign in with Google" (issue #91), a server-side OpenID Connect authorization code flow
# (`apps.accounts.services.google_oauth`). All three come from the environment and are never
# committed; Google sign-in is enabled only when all three are non-empty -- leaving them empty
# (the default) hides the button and makes both routes redirect to the login page with a message,
# so an environment without a Google OAuth client (production, today) needs no code or config
# change. `GOOGLE_REDIRECT_URI` must match, character for character, an "Authorized redirect URI"
# of the OAuth client in Google Cloud, and be this site's `/auth/google/callback` (no trailing
# slash), e.g. `https://app-staging.ziptrigo.com/auth/google/callback`. A partial configuration is
# almost certainly a mistake, so `apps.accounts.checks` warns about it at startup.
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '').strip()
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '').strip()
GOOGLE_REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI', '').strip()

# "Sign in with Microsoft" (issue #156), a server-side OpenID Connect authorization code flow
# against the Microsoft identity platform (`apps.accounts.services.microsoft_oauth`). Same shape as
# Google's: all three come from the environment and are never committed, the feature is on only
# when all three are non-empty, and `apps.accounts.checks` warns about a partial configuration.
# `MICROSOFT_REDIRECT_URI` must match, character for character, a Web redirect URI of the app
# registration in the Microsoft Entra admin center, and be this site's `/auth/microsoft/callback`
# (no trailing slash). `MICROSOFT_TENANT` is the sign-in authority: `common` (the default: work or
# school and personal accounts), `consumers` (personal only), `organizations` (work or school only)
# or one Directory (tenant) ID; it has to agree with the app registration's supported account types.
MICROSOFT_CLIENT_ID = os.getenv('MICROSOFT_CLIENT_ID', '').strip()
MICROSOFT_CLIENT_SECRET = os.getenv('MICROSOFT_CLIENT_SECRET', '').strip()
MICROSOFT_REDIRECT_URI = os.getenv('MICROSOFT_REDIRECT_URI', '').strip()
MICROSOFT_TENANT = os.getenv('MICROSOFT_TENANT', '').strip() or 'common'

# Cloudflare Turnstile captcha on the register page (issue #117,
# `apps.accounts.services.captcha`). On only when both keys are set; leaving both empty (the
# default, and what pytest runs with) hides the widget and skips the check. A partial
# configuration is almost certainly a mistake, so `apps.accounts.checks` warns about it at startup.
# Cloudflare's test keys (always pass / always fail) work on any host, localhost included.
TURNSTILE_SITE_KEY = os.getenv('TURNSTILE_SITE_KEY', '').strip()
TURNSTILE_SECRET_KEY = os.getenv('TURNSTILE_SECRET_KEY', '').strip()

# Base URL used to build absolute links (emails, QR code redirects). It is printed on every tracked
# QR code (`<BASE_URL>/go/<code>`), so prod's stays `https://app.ziptrigo.com` (issue #49).
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
# `infra` repo, `apps/ziptrigo-apps`). Separate from the SES credentials (the default chain: IAM
# Roles Anywhere when deployed) because presigned URLs need a long-lived key, not temporary
# credentials that can expire before the URL does. In dev, point
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


# Receipts (issue #98): AI extraction through an OpenAI-compatible, vision-capable API, and S3
# storage. `RECEIPTS_S3_BUCKET` is the *same* per-environment app bucket as
# `FILE_TRANSFER_S3_BUCKET` (every receipts key is under `receipts/`); the IAM user behind
# `RECEIPTS_AWS_*` is scoped to that prefix (both in the `infra` repo, `apps/ziptrigo-apps`). In dev
# point `RECEIPTS_S3_ENDPOINT_URL` at Floci. Local and pytest need none of these (the AI and the
# storage are faked in tests); `ENVIRONMENT=prod` -- which includes staging -- refuses to start
# without them, below. There is deliberately no feature flag.
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
# Unset: the OpenAI API itself. Set it to use any OpenAI-compatible server.
OPENAI_BASE_URL = os.getenv('OPENAI_BASE_URL', '')
OPENAI_RECEIPTS_MODEL = os.getenv('OPENAI_RECEIPTS_MODEL', '')
# Explicit HTTP timeout and a bounded number of the SDK's own quick retries; longer outages are
# retried by `apps.receipts.services.processing` with a backoff (`ReceiptsSettings.max_ai_attempts`).
RECEIPTS_AI_TIMEOUT_SECONDS = float(os.getenv('RECEIPTS_AI_TIMEOUT_SECONDS', '60'))
RECEIPTS_AI_MAX_RETRIES = int(os.getenv('RECEIPTS_AI_MAX_RETRIES', '2'))

# To-do (issue #183) reads a date, time and repeat rule out of a task title with the same API key
# and base URL (`apps.core.services.ai`), as you type, so it waits far less than Receipts does. The
# model falls back to Receipts'. Not in the prod required-secrets check below: without a key the
# feature is simply off (`apps.todo.checks` warns when a key is set but no model resolves).
OPENAI_TODO_MODEL = os.getenv('OPENAI_TODO_MODEL') or OPENAI_RECEIPTS_MODEL
TODO_AI_TIMEOUT_SECONDS = float(os.getenv('TODO_AI_TIMEOUT_SECONDS', '15'))
TODO_AI_MAX_RETRIES = int(os.getenv('TODO_AI_MAX_RETRIES', '1'))

# Reading a stored receipt (PDF/image/HTML -> text and JPEGs) runs in a child process under a
# resource budget (address space on Linux, CPU seconds, a wall-clock timeout 2-3x the CPU one), so
# a hostile file can't make the `receipts` worker allocate until the container is OOM-killed (see
# `apps.receipts.services.documents`; `apps.receipts.checks` flags odd values). A worker
# running as root starts the child as `RECEIPTS_DOCUMENT_CHILD_USER` (empty: the worker's user).
# Off under pytest: unit tests prepare documents in-process and monkeypatch inside.
RECEIPTS_DOCUMENT_SANDBOX = os.getenv(
    'RECEIPTS_DOCUMENT_SANDBOX', 'False' if _RUNNING_TOOLING else 'True'
).lower() in ('true', '1')
RECEIPTS_DOCUMENT_MEMORY_MB = int(os.getenv('RECEIPTS_DOCUMENT_MEMORY_MB', '1024'))
RECEIPTS_DOCUMENT_CPU_SECONDS = float(os.getenv('RECEIPTS_DOCUMENT_CPU_SECONDS', '25'))
RECEIPTS_DOCUMENT_TIMEOUT_SECONDS = float(os.getenv('RECEIPTS_DOCUMENT_TIMEOUT_SECONDS', '60'))
RECEIPTS_DOCUMENT_CHILD_USER = os.getenv('RECEIPTS_DOCUMENT_CHILD_USER', 'nobody')

RECEIPTS_S3_BUCKET = os.getenv('RECEIPTS_S3_BUCKET', '')
RECEIPTS_S3_REGION = os.getenv('RECEIPTS_S3_REGION', AWS_REGION)
RECEIPTS_S3_ENDPOINT_URL = os.getenv('RECEIPTS_S3_ENDPOINT_URL') or None
RECEIPTS_AWS_ACCESS_KEY_ID = os.getenv('RECEIPTS_AWS_ACCESS_KEY_ID', '')
RECEIPTS_AWS_SECRET_ACCESS_KEY = os.getenv('RECEIPTS_AWS_SECRET_ACCESS_KEY', '')
# The address mail is received at: `receipts+<code>@<domain of this>`, e.g.
# `receipts@ziptrigo.com`, or `receipts-staging@ziptrigo.com` on staging.
RECEIPTS_INBOUND_ADDRESS = os.getenv('RECEIPTS_INBOUND_ADDRESS', 'receipts@ziptrigo.com')

if os.getenv('ENVIRONMENT') == 'prod':
    _insecure = insecure_secrets(
        {
            'OPENAI_API_KEY': OPENAI_API_KEY,
            'OPENAI_RECEIPTS_MODEL': OPENAI_RECEIPTS_MODEL,
            'RECEIPTS_S3_BUCKET': RECEIPTS_S3_BUCKET,
            'RECEIPTS_AWS_ACCESS_KEY_ID': RECEIPTS_AWS_ACCESS_KEY_ID,
            'RECEIPTS_AWS_SECRET_ACCESS_KEY': RECEIPTS_AWS_SECRET_ACCESS_KEY,
        }
    )
    if _insecure:
        raise ImproperlyConfigured(
            f'Set real values for {", ".join(_insecure)} in production; '
            'they are missing or still placeholders.'
        )


# To-do attachments (issue #184): S3 storage. `TODO_S3_BUCKET` is the *same* per-environment app
# bucket as `FILE_TRANSFER_S3_BUCKET` and `RECEIPTS_S3_BUCKET` (every key is under `todo/`); the
# IAM user behind `TODO_AWS_*` is scoped to that prefix (the `infra` repo, `apps/ziptrigo-apps`,
# issue #185). In dev point `TODO_S3_ENDPOINT_URL` at Floci. Local and pytest need none of these
# (the storage is faked in tests); `ENVIRONMENT=prod` -- which includes staging -- refuses to start
# without the bucket and both keys, below.
TODO_S3_BUCKET = os.getenv('TODO_S3_BUCKET', '')
TODO_S3_REGION = os.getenv('TODO_S3_REGION', AWS_REGION)
TODO_S3_ENDPOINT_URL = os.getenv('TODO_S3_ENDPOINT_URL') or None
TODO_AWS_ACCESS_KEY_ID = os.getenv('TODO_AWS_ACCESS_KEY_ID', '')
TODO_AWS_SECRET_ACCESS_KEY = os.getenv('TODO_AWS_SECRET_ACCESS_KEY', '')

if os.getenv('ENVIRONMENT') == 'prod':
    _insecure = insecure_secrets(
        {
            'TODO_S3_BUCKET': TODO_S3_BUCKET,
            'TODO_AWS_ACCESS_KEY_ID': TODO_AWS_ACCESS_KEY_ID,
            'TODO_AWS_SECRET_ACCESS_KEY': TODO_AWS_SECRET_ACCESS_KEY,
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
# objects). Receipts has its own `receipts` queue, served by a second `db_worker --queue-name
# receipts`, so slow AI/PDF work can't delay file transfer's emails and zip builds. Tests run
# everything inline via `ImmediateBackend` so they don't depend on a worker.
TASKS = {
    'default': {
        'BACKEND': (
            'django.tasks.backends.immediate.ImmediateBackend'
            if _RUNNING_TOOLING
            else 'django_tasks_db.DatabaseBackend'
        ),
        'QUEUES': ['default', 'receipts'],
    }
}

# apps.core.scheduler: how often (seconds) a scheduler runner -- the `run_scheduler` management
# command's, or the thread `start_scheduler_thread()` starts inside each gunicorn worker -- checks
# for due jobs. Each job's own interval (in `JobSpec`) controls how often it actually runs.
SCHEDULER_TICK_SECONDS = int(os.getenv('SCHEDULER_TICK_SECONDS', '30'))

# Start `apps.core.scheduler.start_scheduler_thread()` inside every gunicorn worker (see
# `gunicorn.conf.py`). Off by default so a local checkout, `runserver` and pytest never run jobs.
SCHEDULER_ENABLED = os.getenv('SCHEDULER_ENABLED', 'False').lower() in ('true', '1')

# Everything goes to the console (the container logs). Django's own default config sends the
# `django` logger to a console handler that only runs with DEBUG=True and to `mail_admins`
# (`ADMINS` is empty), and because that logger has handlers, Python's last-resort stderr handler
# never fires: with DEBUG=False every unhandled view exception (`django.request`), `django.security`
# warning and so on was silently dropped. So `django` is redirected to our console handler
# (`propagate: False`: one line per record, also with DEBUG=True, where Django's handler would
# otherwise print it a second time through root), `apps` logs at INFO (scheduler lines, job logs,
# the rate limiter's "over limit" lines), and the root logger catches WARNING+ from everything
# else (`django_tasks` worker failures, `authlib`, `botocore`, ...). `apps` also stops at its own
# handler so root doesn't repeat it.
#
# Two `django` children are tuned. `django.request` is held at ERROR: Django logs *every* response
# >= 400 there at WARNING (each htmx 422, 429, API 4xx and bot 404), which would bury the real
# 500s, and gunicorn's access log already records those; ERROR keeps 5xx and unhandled exceptions
# (no handler of its own, it propagates to `django`'s). `django.security.DisallowedHost` fires at
# ERROR for every scanner sending a bad Host header, so it is dropped (the other
# `django.security.*` loggers still print). `django.server` (local `runserver` request lines) is
# re-declared with Django's own formatter, since configuring `django` resets that child logger.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'plain': {'format': '%(levelname)s %(name)s: %(message)s'},
        'django.server': {
            '()': 'django.utils.log.ServerFormatter',
            'format': '[{server_time}] {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'},
        'django.server': {
            'class': 'logging.StreamHandler',
            'formatter': 'django.server',
        },
        'null': {'class': 'logging.NullHandler'},
    },
    'loggers': {
        'apps': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'django': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'django.request': {'level': 'ERROR'},
        'django.security.DisallowedHost': {'handlers': ['null'], 'propagate': False},
        'django.server': {'handlers': ['django.server'], 'level': 'INFO', 'propagate': False},
    },
    'root': {'handlers': ['console'], 'level': 'WARNING'},
}


# QR code settings
QR_CODE_REDIRECT_PATH = '/go/'

# Short URLs (issue #162): a short link is `<BASE_URL>/s/<code>`. Like `/go/`, the path is
# printed and shared, so it must never move.
SHORT_URL_PATH = '/s/'

# MaxMind GeoLite2-Country database (`.mmdb`) used to tell the country of a click
# (`apps.short_url.services.geoip`). Empty or a missing file: every country is "unknown" and
# `manage.py check` warns (`short_url.W001`); nothing else is affected. See README, "Short URLs".
GEOIP_PATH = os.getenv('GEOIP_PATH', '')


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
        'feedback.Feedback': 'fas fa-comment-dots',
        'file_transfer.Transfer': 'fas fa-paper-plane',
        'file_transfer.DownloadEvent': 'fas fa-download',
        'file_transfer.FileTransferSettings': 'fas fa-sliders-h',
        'receipts.Receipt': 'fas fa-receipt',
        'receipts.ReceiptsAccount': 'fas fa-inbox',
        'receipts.ReceiptsSettings': 'fas fa-sliders-h',
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
