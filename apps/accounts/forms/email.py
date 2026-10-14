from ..models import User


class NormalizedEmailMixin:
    """`clean_email` for a form with an `email` field: the address is normalized the way accounts
    are stored (`UserManager.normalize_email` lower-cases the domain, not the local part), so
    `alice@Example.com` finds the account `alice@example.com` on every web form, like the API's
    `EmailStr` does. The local part is left alone on purpose: that is the stored spelling, and
    lookups by it stay exact.
    """

    cleaned_data: dict

    def clean_email(self) -> str:
        return User.objects.normalize_email(self.cleaned_data['email'])
