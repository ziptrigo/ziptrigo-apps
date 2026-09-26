"""Admin registrations for the billing app, plus the manual credit adjustment tool."""

from django import forms
from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import path

from apps.accounts.models import User
from apps.core.admin_site import custom_admin_site

from .models import CreditAccount, CreditTransaction, CreditTransactionType
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
    """Admin interface for CreditTransaction, with a page to adjust a user's credits."""

    list_display = ['id', 'user', 'amount', 'type', 'source', 'description', 'created_at']
    list_filter = ['type', 'source', 'created_at']
    search_fields = ['user__email', 'user__name', 'description']
    readonly_fields = ['id', 'created_at']
    date_hierarchy = 'created_at'

    def get_urls(self):
        custom_urls = [
            path(
                'adjust/',
                self.admin_site.admin_view(self.adjust_credits_view),
                name='billing_credittransaction_adjust',
            ),
        ]
        return custom_urls + super().get_urls()

    def adjust_credits_view(self, request: HttpRequest) -> HttpResponse:
        """Manually add or spend a user's credits, always recorded as an `adjustment`."""
        if not self.has_add_permission(request):
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

        try:
            target = User.objects.get(email=user_email)
        except User.DoesNotExist:
            messages.error(request, f'No user found with email: {user_email}')
            return False

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


custom_admin_site.register(CreditTransaction, CreditTransactionAdmin)
custom_admin_site.register(CreditAccount, CreditAccountAdmin)
