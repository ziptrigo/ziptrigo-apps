from django import forms

from ..services.terms import TERMS_REQUIRED_MESSAGE


class AcceptTermsForm(forms.Form):
    """The one-time accept page (issue #96): the same required checkbox as the sign-up form."""

    accept_terms = forms.BooleanField(
        required=True, label='Terms', error_messages={'required': TERMS_REQUIRED_MESSAGE}
    )
