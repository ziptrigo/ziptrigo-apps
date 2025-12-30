"""
Shared base Django settings for ziptrigo-apps services.

This module contains common configuration that is shared across all services.
Individual services should import from this module and override/extend as needed.
"""

# Application definition - common middleware
COMMON_MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators
# Services can override this if they need different validation rules
COMMON_AUTH_PASSWORD_VALIDATORS = [
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


# Common template context processors
COMMON_TEMPLATE_CONTEXT_PROCESSORS = [
    'django.template.context_processors.debug',
    'django.template.context_processors.request',
    'django.contrib.auth.context_processors.auth',
    'django.contrib.messages.context_processors.messages',
]


# Common installed apps (base Django)
COMMON_INSTALLED_APPS = [
    'jazzmin',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]


# Static files configuration (common pattern)
# Services should override STATIC_URL, STATICFILES_DIRS, and STATIC_ROOT
COMMON_STATIC_URL = '/static/'


# Common Jazzmin base settings
# Services should extend/override as needed
COMMON_JAZZMIN_SETTINGS = {
    'show_sidebar': True,
    'navigation_expanded': True,
    'theme': 'default',
    'dark_mode_theme': 'darkly',
    'default_icon_parents': 'fas fa-chevron-right',
    'default_icon_children': 'fas fa-arrow-right',
    'related_modal_active': True,
    'custom_css': 'css/jazzmin_custom.css',
    'custom_js': 'js/admin_theme_toggle.js',
    'user_avatar': None,
    'login_logo': 'images/logo_128x128.png',
    'site_logo': 'images/logo_128x128.png',
}
