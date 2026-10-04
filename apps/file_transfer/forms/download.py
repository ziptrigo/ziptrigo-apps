"""The public download page's password prompt (spec section 3)."""

from django import forms

from ._styling import StyledFormMixin


class DownloadPasswordForm(StyledFormMixin, forms.Form):
    password = forms.CharField(widget=forms.PasswordInput, label='Password')
