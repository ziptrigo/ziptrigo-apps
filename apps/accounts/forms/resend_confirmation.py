from django import forms

from .email import NormalizedEmailMixin


class ResendConfirmationForm(NormalizedEmailMixin, forms.Form):
    """Request a fresh confirmation email from the expired-confirmation-link page (issue #52).
    Same rules as `ResendConfirmationRequest` (`POST /api/auth/resend-confirmation`) -- whether the
    address is registered, or already confirmed, is never revealed (CLAUDE.md).
    """

    email = forms.EmailField()
