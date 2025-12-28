import os
from typing import List, Tuple

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import path
from django.utils import timezone

from .models import CreditTransaction, InsufficientCreditsError, QRCode, User
from .services.email_service import send_email


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Admin interface for User."""

    # Extend the default fieldsets with our custom fields.
    fieldsets = DjangoUserAdmin.fieldsets + (  # type: ignore
        (
            'Custom Fields',
            {
                'fields': (
                    'name',
                    'email_confirmed',
                    'email_confirmed_at',
                    'credits',
                )
            },
        ),
    )  # type: ignore[assignment,operator]

    list_display = [
        'username',
        'email',
        'name',
        'credits',
        'email_confirmed',
        'is_staff',
        'is_active',
    ]
    list_filter = ['email_confirmed', 'is_staff', 'is_active', 'is_superuser']
    readonly_fields = ['email_confirmed_at']


@admin.register(QRCode)
class QRCodeAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'name',
        'content_preview',
        'qr_format',
        'created_by',
        'scan_count',
        'created_at',
        'deleted_at',
    ]
    list_filter = ['qr_format', 'use_url_shortening', 'created_at', 'deleted_at']
    search_fields = ['content', 'original_url', 'short_code']
    readonly_fields = [
        'id',
        'created_at',
        'updated_at',
        'scan_count',
        'last_scanned_at',
        'deleted_at',
    ]

    @admin.display(description='Content')
    def content_preview(self, obj: QRCode) -> str:
        return obj.content[:50] + '...' if len(obj.content) > 50 else obj.content


@admin.register(CreditTransaction)
class CreditTransactionAdmin(admin.ModelAdmin):
    """Admin interface for CreditTransaction."""

    list_display = ['id', 'user', 'amount', 'type', 'description', 'created_at']
    list_filter = ['type', 'created_at']
    search_fields = ['user__email', 'user__username', 'description']
    readonly_fields = ['id', 'created_at']


class TestEmailForm(forms.Form):
    recipient: forms.EmailField = forms.EmailField(
        label='Recipient email',
        required=True,
        help_text='Email address to send the test email to.',
        widget=forms.EmailInput(attrs={'size': '60'}),
    )


class CreditAdjustmentForm(forms.Form):
    user_email: forms.EmailField = forms.EmailField(
        label='User email',
        required=True,
        help_text='Email address of the user to adjust.',
        widget=forms.EmailInput(attrs={'size': '60'}),
    )
    direction: forms.ChoiceField = forms.ChoiceField(
        label='Direction',
        required=True,
        choices=[('add', 'Add'), ('spend', 'Spend')],
        initial='add',
    )
    amount: forms.IntegerField = forms.IntegerField(
        label='Amount',
        required=True,
        min_value=1,
        help_text='Number of credits to add/spend. Must be > 0.',
    )
    description: forms.CharField = forms.CharField(
        label='Description',
        required=False,
        max_length=255,
        help_text='Optional description for the ledger entry.',
        widget=forms.TextInput(attrs={'size': '60'}),
    )


class CustomAdminSite(admin.AdminSite):
    """Custom admin site with additional tools."""

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('tools/', self.admin_view(self.tools_view), name='admin_tools'),
        ]
        return custom_urls + urls

    def tools_view(self, request: HttpRequest) -> HttpResponse:
        """Custom admin page for various tools."""
        environment_variables = None
        environment = os.getenv('ENVIRONMENT')

        initial_email = getattr(request.user, 'email', '') or ''

        # Restrict access strictly to superusers
        if not request.user.is_superuser:
            messages.error(request, 'You do not have permission to access this page.')
            email_form = TestEmailForm(initial={'recipient': initial_email})
            credit_form = CreditAdjustmentForm()
            context = {
                **self.each_context(request),
                'title': 'Admin Tools',
                'email_form': email_form,
                'credit_form': credit_form,
                'environment': environment,
                'environment_variables': None,
            }
            return render(request, 'admin/tools.html', context, status=403)

        email_form = TestEmailForm(initial={'recipient': initial_email})
        credit_form = CreditAdjustmentForm()

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
                    if successes:
                        messages.success(
                            request,
                            f'Test email sent to {recipient} via {successes} backend(s) '
                            f'({failures} failure(s)).',
                        )
                    else:
                        messages.error(
                            request, f'All email backends failed ({failures} failure(s)).'
                        )
                    email_form = TestEmailForm(initial={'recipient': recipient})
                except Exception as e:
                    messages.error(request, f'Failed to send email: {str(e)}')
            else:
                messages.error(request, 'Please correct the errors below.')
        elif request.method == 'POST' and 'show_environment' in request.POST:
            # Show all environment variables
            try:
                # Convert to a sorted list of (key, value) for deterministic display
                raw_env: List[Tuple[str, str]] = sorted(
                    os.environ.items(), key=lambda it: it[0].lower()
                )

                # Exclude specific keys entirely
                EXCLUDED_KEYS = {'DJANGO_SECRET_KEY'}
                WHITE_LISTED_KEYS = {'AWS_REGION', 'AWS_SES_SENDER', 'AWS_S3_URI'}

                def is_sensitive(key: str) -> bool:
                    k = key.upper()
                    if k in WHITE_LISTED_KEYS:
                        return False
                    if any(s in k for s in ['SECRET', 'PASSWORD', 'TOKEN']):
                        return True
                    # Avoid over-masking: 'KEY' alone can be too broad; include if endswith _KEY
                    if k.endswith('_KEY'):
                        return True
                    if k.startswith(('AWS_', 'GCP_', 'AZURE_')):
                        return True
                    return False

                def mask(value: str) -> str:
                    s = str(value)
                    if len(s) <= 4:
                        return '*' * len(s)
                    segment = max(int(len(s) / 5), 2)
                    return f'{s[:segment]}{"*"*(len(s)-segment*2)}{s[-segment:]}'

                # Build final list with masking
                environment_variables = []
                for key, value in raw_env:
                    if key in EXCLUDED_KEYS:
                        continue
                    display_value = mask(value) if is_sensitive(key) else value
                    environment_variables.append((key, display_value))
                messages.success(request, 'Environment variables loaded.')
            except Exception as e:
                messages.error(request, f'Failed to load environment variables: {str(e)}')
        elif request.method == 'POST' and 'adjust_credits' in request.POST:
            credit_form = CreditAdjustmentForm(request.POST)
            if credit_form.is_valid():
                user_email = credit_form.cleaned_data['user_email']
                direction = credit_form.cleaned_data['direction']
                amount = int(credit_form.cleaned_data['amount'])
                description = str(credit_form.cleaned_data.get('description') or '').strip()

                try:
                    target = User.objects.get(email=user_email)
                except User.DoesNotExist:
                    messages.error(request, f'No user found with email: {user_email}')
                else:
                    if not description:
                        description = 'Admin adjustment'

                    try:
                        if direction == 'add':
                            target.add_credits(
                                amount, tx_type='adjustment', description=description
                            )
                        else:
                            target.spend_credits(
                                amount, tx_type='adjustment', description=description
                            )
                        messages.success(
                            request,
                            f'Adjusted credits for {target.email}. New balance: {target.credits}.',
                        )
                        credit_form = CreditAdjustmentForm()
                    except InsufficientCreditsError:
                        messages.error(request, 'Insufficient credits for this spend adjustment.')
                    except Exception as e:
                        messages.error(request, f'Failed to adjust credits: {str(e)}')
            else:
                messages.error(request, 'Please correct the errors below.')

        context = {
            **self.each_context(request),
            'title': 'Admin Tools',
            'email_form': email_form,
            'credit_form': credit_form,
            'environment': environment,
            'environment_variables': environment_variables,
        }
        return render(request, 'admin/tools.html', context)


# Create custom admin site instance
custom_admin_site = CustomAdminSite(name='custom_admin')

# Register models with custom admin site
custom_admin_site.register(User, UserAdmin)
custom_admin_site.register(QRCode, QRCodeAdmin)
custom_admin_site.register(CreditTransaction, CreditTransactionAdmin)
