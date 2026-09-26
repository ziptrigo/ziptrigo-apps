"""The site-wide admin site.

Every app registers its `ModelAdmin`s on `custom_admin_site` from its own `admin.py`; this module
only owns the site itself and the cross-cutting admin tools page.
"""

from __future__ import annotations

import os
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.models import PermissionsMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import path
from django.utils import timezone

from .services.email import send_email


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

                excluded_keys = {'DJANGO_SECRET_KEY'}
                white_listed_keys = {'AWS_REGION', 'AWS_SES_SENDER', 'AWS_S3_URI'}

                def is_sensitive(key: str) -> bool:
                    k = key.upper()
                    if k in white_listed_keys:
                        return False
                    if any(flag in k for flag in ['SECRET', 'PASSWORD', 'TOKEN']):
                        return True
                    if k.endswith('_KEY'):
                        return True
                    if k.startswith(('AWS_', 'GCP_', 'AZURE_')):
                        return True
                    return False

                def mask(value: Any) -> str:
                    text = str(value)
                    if len(text) <= 4:
                        return '*' * len(text)
                    segment = max(len(text) // 5, 2)
                    hidden_len = max(len(text) - (segment * 2), 0)
                    hidden = '*' * hidden_len
                    return f'{text[:segment]}{hidden}{text[-segment:]}'

                environment_variables = []
                for key, value in raw_env:
                    if key in excluded_keys:
                        continue
                    display_value = mask(value) if is_sensitive(key) else value
                    environment_variables.append((key, display_value))
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
