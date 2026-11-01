"""Admin registrations for the billing app, plus the manual credit adjustment tool. The promo code
section (issue #70) is in `admin_promo.py`."""

from django import forms
from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import path

from apps.accounts.http import AuthenticatedHttpRequest
from apps.accounts.models import User
from apps.core.admin_singleton import SingletonSettingsAdmin
from apps.core.admin_site import custom_admin_site

from .admin_promo import PromoCodeAdmin, PromoRedemptionAdmin
from .models import (
    BillingSettings,
    CreditAccount,
    CreditTransaction,
    CreditTransactionType,
    PromoCode,
    PromoRedemption,
    WelcomeCredit,
)
from .services import InsufficientCreditsError, add_credits, get_balance, spend_credits


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


class CreditTransactionAdmin(admin.ModelAdmin):
    """Read-only ledger, with a page to adjust a user's credits.

    Rows are never added, edited or deleted by hand: the ledger must add up to the balance, and
    both only change through `apps.billing.services`. The adjustment tool is the way in.
    """

    list_display = ['id', 'user', 'amount', 'type', 'source', 'description', 'created_at']
    list_filter = ['type', 'source', 'created_at']
    search_fields = ['user__email', 'user__name', 'description']
    readonly_fields = ['id', 'created_at']
    date_hierarchy = 'created_at'

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: CreditTransaction | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: CreditTransaction | None = None
    ) -> bool:
        return False

    def get_urls(self):
        custom_urls = [
            path(
                'adjust/',
                self.admin_site.admin_view(self.adjust_credits_view),
                name='billing_credittransaction_adjust',
            ),
        ]
        return custom_urls + super().get_urls()

    def adjust_credits_view(self, request: AuthenticatedHttpRequest) -> HttpResponse:
        """Manually add or spend a user's credits, always recorded as an `adjustment`."""
        # The tool writes ledger rows, so it needs the permission to add them. That is checked
        # with `has_perm` directly rather than `has_add_permission`, which is False on purpose
        # (the change form must not create rows by hand) and would lock out everyone, superusers
        # included. Follow-up: a dedicated `adjust_credits` permission (as file_transfer has
        # `takedown_transfer`) would stop overloading `add_credittransaction`; it needs a data
        # migration to grant it to whoever holds the add permission today, so it isn't done here.
        if not request.user.has_perm('billing.add_credittransaction'):
            messages.error(request, 'You do not have permission to adjust credits.')
            status = 403
            credit_form = CreditAdjustmentForm()
        else:
            status = 200
            credit_form = CreditAdjustmentForm(request.POST or None)
            if request.method == 'POST' and credit_form.is_valid():
                if self._apply_adjustment(request, credit_form.cleaned_data):
                    credit_form = CreditAdjustmentForm()
            elif request.method == 'POST':
                messages.error(request, 'Please correct the errors below.')

        context = {
            **self.admin_site.each_context(request),
            'title': 'Adjust credits',
            'opts': self.model._meta,
            'credit_form': credit_form,
        }
        return render(
            request, 'admin/billing/credittransaction/adjust_credits.html', context, status=status
        )

    @staticmethod
    def _apply_adjustment(request: HttpRequest, data: dict) -> bool:
        user_email = data['user_email']
        amount = int(data['amount'])
        description = str(data.get('description') or '').strip() or 'Admin adjustment'

        # Case-insensitive, like signup and the user admin (CLAUDE.md "Email case"); an address that
        # matches several legacy rows only in case must be typed exactly as stored.
        matches = list(User.objects.filter(email__iexact=user_email))
        if len(matches) > 1:
            exact = [u for u in matches if u.email == user_email]
            if len(exact) != 1:
                messages.error(
                    request,
                    f'Several accounts match {user_email} in different case; type the address '
                    'exactly as it is stored.',
                )
                return False
            matches = exact
        if not matches:
            messages.error(request, f'No user found with email: {user_email}')
            return False
        target = matches[0]

        try:
            if data['direction'] == 'add':
                add_credits(
                    target,
                    amount,
                    tx_type=CreditTransactionType.ADJUSTMENT,
                    description=description,
                )
            else:
                spend_credits(
                    target,
                    amount,
                    tx_type=CreditTransactionType.ADJUSTMENT,
                    description=description,
                )
        except InsufficientCreditsError:
            messages.error(request, 'Insufficient credits for this spend adjustment.')
            return False

        messages.success(
            request,
            f'Adjusted credits for {target.email}. New balance: {get_balance(target)}.',
        )
        return True


class CreditAccountAdmin(admin.ModelAdmin):
    """Read-only view of balances; change them through the credit adjustment tool."""

    list_display = ['user', 'balance', 'updated_at']
    search_fields = ['user__email', 'user__name']
    readonly_fields = ['user', 'balance', 'updated_at']

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: CreditAccount | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: CreditAccount | None = None) -> bool:
        return False


class BillingSettingsAdmin(SingletonSettingsAdmin):
    """Singleton settings page (issue #98): there's only ever the one row
    `BillingSettings.load()` creates."""


custom_admin_site.register(CreditTransaction, CreditTransactionAdmin)
custom_admin_site.register(CreditAccount, CreditAccountAdmin)
custom_admin_site.register(BillingSettings, BillingSettingsAdmin)
custom_admin_site.register(PromoCode, PromoCodeAdmin)
custom_admin_site.register(PromoRedemption, PromoRedemptionAdmin)


class WelcomeCreditAdmin(admin.ModelAdmin):
    """Read-only view of who got, or was refused, the welcome credits (issue #160)."""

    list_display = ['user', 'status', 'reason', 'credits', 'created_at', 'decided_at']
    list_filter = ['status', 'reason', 'created_at']
    search_fields = ['user__email', 'user__name']
    readonly_fields = [
        'id',
        'user',
        'status',
        'reason',
        'credits',
        'transaction',
        'refusal_emailed_at',
        'created_at',
        'decided_at',
    ]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: WelcomeCredit | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: WelcomeCredit | None = None) -> bool:
        return False


custom_admin_site.register(WelcomeCredit, WelcomeCreditAdmin)
