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

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': PROJECT_ROOT / 'db.sqlite3',
    }
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

# Email confirmation and password reset settings
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
# `infra` repo, `apps/ziptrigo`). Separate from the SES credentials above because presigned URLs
# need long-lived keys, not the temporary ones from assuming `AWS_ROLE`. In dev, point
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
