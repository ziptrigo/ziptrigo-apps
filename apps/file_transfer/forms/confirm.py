"""The anonymous send page's inline "enter your code" form (spec section 2 step 4)."""

from django import forms


class ConfirmCodeForm(forms.Form):
    code = forms.CharField(label='Confirmation code', max_length=10)
