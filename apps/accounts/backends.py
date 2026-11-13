from django.contrib.auth.backends import ModelBackend

#: Dotted path `login()` records on the session for a sign-in that doesn't go through
#: `authenticate` (Google, Microsoft, the emailed code or link). One place, so they can't drift.
LOGIN_BACKEND = 'apps.accounts.backends.EmailBackend'


class EmailBackend(ModelBackend):
    """Django's `ModelBackend`, which already logs in by `User.USERNAME_FIELD` (the email).

    Deliberately no overrides: `authenticate` hashes the password once for an unknown email (so
    a login doesn't reveal whether an address has an account, issue #125) and, with `get_user`,
    refuses `is_active=False` users (so unticking "Active" in the admin also ends their sessions).
    The class is kept because stored sessions and `LOGIN_BACKEND` name its path.
    """
