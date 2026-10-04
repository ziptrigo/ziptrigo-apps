"""The anonymous send page's inline "enter your code" form (spec section 2 step 4)."""

from django import forms

from ._styling import StyledFormMixin


class ConfirmCodeForm(StyledFormMixin, forms.Form):
    code = forms.CharField(label='Confirmation code', max_length=10)
