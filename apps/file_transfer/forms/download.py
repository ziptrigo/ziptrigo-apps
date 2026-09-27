"""The public download page's password prompt (spec section 3)."""

from django import forms


class DownloadPasswordForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput, label='Password')
