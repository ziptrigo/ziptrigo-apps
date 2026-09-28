from django import forms


class ForgotPasswordForm(forms.Form):
    """Request a password-reset email for the web UI (issue #52). Same rules as
    `PasswordResetRequest` (`POST /api/auth/forgot-password`) -- deliberately just an email format
    check: whether the account exists is never revealed (CLAUDE.md), so there's nothing further to
    validate here.
    """

    email = forms.EmailField()
