"""The site-wide admin site.

Every app registers its `ModelAdmin`s on `custom_admin_site` from its own `admin.py`; this module
only owns the site itself and the cross-cutting admin tools page.
"""

from __future__ import annotations

import os
import re

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.models import PermissionsMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import path
from django.utils import timezone

from .services.email import send_email

# "Show Environment" on the tools page. A name is sensitive if any of its `_`-separated words is
# one of these (so `FILE_TRANSFER_AWS_ACCESS_KEY_ID` and `JWT_SECRET` are, `KEYBOARD_LAYOUT` is
# not), unless it is a known harmless setting or ends in a duration/count word (the TTLs and
# limits named after what they bound, like `PASSWORD_RESET_TOKEN_TTL_HOURS`).
SENSITIVE_NAME_WORDS = frozenset(
    {'SECRET', 'PASSWORD', 'PASSWD', 'TOKEN', 'KEY', 'CREDENTIAL', 'CREDENTIALS', 'PRIVATE'}
)
HARMLESS_NAME_SUFFIXES = ('_SECONDS', '_MINUTES', '_HOURS', '_DAYS', '_TTL', '_ALGORITHM')
HARMLESS_NAMES = frozenset({'AWS_REGION', 'AWS_SES_SENDER', 'AWS_S3_URI'})
# `scheme://<userinfo>@rest`, the userinfo running to the *last* `@`: an unencoded `@` or `/` in a
# password then over-masks rather than leaking part of it.
_URL_WITH_USERINFO = re.compile(r'^([a-z][a-z0-9+.-]*://)(.*)@(.*)$', re.IGNORECASE | re.DOTALL)


def is_sensitive_env_name(key: str) -> bool:
    name = key.upper()
    if name in HARMLESS_NAMES or name.endswith(HARMLESS_NAME_SUFFIXES):
        return False
    if name.startswith(('AWS_', 'GCP_', 'AZURE_')):
        return True
    return any(word in SENSITIVE_NAME_WORDS for word in name.split('_'))


def mask_secret(value: str) -> str:
    """Hide a secret, keeping at most its last 4 characters (only for long values) so two
    deployments' values can still be told apart. The mask has a fixed length, hiding the size."""
    if len(value) < 16:
        return '********'
    return f'********{value[-4:]}'


def mask_url_password(value: str) -> str:
    """`scheme://user:password@host/...` with the password masked (a userinfo without a user name,
    like `redis://token@host`, is masked whole); anything else unchanged."""
    match = _URL_WITH_USERINFO.match(value)
    if not match:
        return value
    scheme, userinfo, rest = match.groups()
    user, colon, _ = userinfo.partition(':')
    masked = f'{user}:********' if colon else '********'
    return f'{scheme}{masked}@{rest}'


def display_env_value(key: str, value: str) -> str:
    """What the tools page shows for one environment variable: masked when the name looks like a
    secret, and with any password embedded in a URL (`DATABASE_URL`, `CACHE_URL`) masked whatever
    the name."""
    if is_sensitive_env_name(key):
        return mask_secret(value)
    return mask_url_password(value)


class AuthenticatedHttpRequest(HttpRequest):
    """`HttpRequest` typed with the `.user` attribute `AuthenticationMiddleware` adds at runtime.

    `HttpRequest` itself doesn't declare `.user` -- `ty` has no equivalent of django-stubs' plugin
    to know middleware adds it, so admin methods reached through `admin_view()` (which redirects
    unauthenticated/non-staff requests before the view body runs, guaranteeing a real user here)
    need this instead. Typed as `PermissionsMixin` rather than the concrete user model because
    `core` sits below `accounts` and must not import it.
    """

    user: PermissionsMixin


class TestEmailForm(forms.Form):
    """Simple form for sending a test email."""

    recipient: forms.EmailField = forms.EmailField(
        label='Recipient email',
        required=True,
        help_text='Email address to send the test email to.',
        widget=forms.EmailInput(attrs={'size': '60'}),
    )


class CustomAdminSite(admin.AdminSite):
    """Custom admin site with additional tools."""

    site_header = 'ZipTrigo Administration'
    site_title = 'ZipTrigo Admin'
    index_title = 'Dashboard'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('tools/', self.admin_view(self.tools_view), name='admin_tools'),
        ]
        return custom_urls + urls

    def tools_view(self, request: AuthenticatedHttpRequest) -> HttpResponse:
        """Custom admin page for various tools."""

        environment = os.getenv('ENVIRONMENT')
        environment_variables: list[tuple[str, str]] | None = None
        initial_email = getattr(request.user, 'email', '') or ''
        email_form: forms.Form = TestEmailForm(initial={'recipient': initial_email})

        if not request.user.is_superuser:
            messages.error(request, 'You do not have permission to access this page.')
            context = {
                **self.each_context(request),
                'title': 'Admin Tools',
                'email_form': email_form,
                'environment': environment,
                'environment_variables': None,
            }
            return render(request, 'admin/tools.html', context, status=403)

        if request.method == 'POST' and 'send_test_email' in request.POST:
            email_form = TestEmailForm(request.POST)
            if email_form.is_valid():
                recipient = email_form.cleaned_data['recipient']
                current_datetime = timezone.now().strftime('%Y-%m-%d %H:%M:%S %Z')

                subject = 'Test email from the admin panel'
                text_body = (
                    'This is a test email.\n'
                    'Sent from the Django admin panel.\n'
                    f'\nSent: {current_datetime}'
                )

                try:
                    successes, failures = send_email(
                        to=recipient,
                        subject=subject,
                        text_body=text_body,
                    )
                except Exception as exc:  # pragma: no cover
                    messages.error(request, f'Failed to send email: {exc}')
                else:
                    if successes:
                        messages.success(
                            request,
                            (
                                f'Test email sent to {recipient} via {successes} backend(s) '
                                f'({failures} failure(s)).'
                            ),
                        )
                    else:
                        messages.error(
                            request,
                            f'All email backends failed ({failures} failure(s)).',
                        )
                    email_form = TestEmailForm(initial={'recipient': recipient})
            else:
                messages.error(request, 'Please correct the errors below.')
        elif request.method == 'POST' and 'show_environment' in request.POST:
            try:
                raw_env = sorted(os.environ.items(), key=lambda it: it[0].lower())

                environment_variables = []
                for key, value in raw_env:
                    environment_variables.append((key, display_env_value(key, value)))
                messages.success(request, 'Environment variables loaded.')
            except Exception as exc:  # pragma: no cover
                messages.error(request, f'Failed to load environment variables: {exc}')

        context = {
            **self.each_context(request),
            'title': 'Admin Tools',
            'email_form': email_form,
            'environment': environment,
            'environment_variables': environment_variables,
        }
        return render(request, 'admin/tools.html', context)


custom_admin_site = CustomAdminSite(name='custom_admin')
