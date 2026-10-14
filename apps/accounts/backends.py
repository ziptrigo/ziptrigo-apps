from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    """Django's `ModelBackend`, which already logs in by `User.USERNAME_FIELD` (the email).

    Deliberately no overrides: `authenticate` hashes the password once for an unknown email (so
    a login doesn't reveal whether an address has an account, issue #125) and, with `get_user`,
    refuses `is_active=False` users (so unticking "Active" in the admin also ends their sessions).
    The class is kept because stored sessions and `google_oauth.LOGIN_BACKEND` name its path.
    """
