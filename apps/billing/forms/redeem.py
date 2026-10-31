from django import forms

from ..models.promo import CODE_MAX_LENGTH


class RedeemForm(forms.Form):
    """The promo code form on the account and credits pages (issue #70). Only gathers the code;
    `apps.billing.services.promo.redeem` decides whether it is valid."""

    code = forms.CharField(max_length=CODE_MAX_LENGTH, label='Promo code')
    next = forms.CharField(required=False, max_length=500)

    def clean_code(self) -> str:
        return (self.cleaned_data.get('code') or '').strip()
