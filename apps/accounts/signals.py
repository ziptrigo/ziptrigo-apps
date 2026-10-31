"""Signals `accounts` emits so a product can react to a change on a user without `accounts`
importing that product (see the dependency layering in `CLAUDE.md`: only products may import
`accounts`, never the other way).
"""

from django.dispatch import Signal

#: Sent, on commit, by `apps.accounts.services.timezones.set_timezone` when a user's time zone
#: actually changes (never for a save of the same value). `sender` is `User`; providing args:
#: `user` (the `accounts.User`), `old` and `new` (the zone names, `''` meaning "not set yet",
#: which is treated as UTC).
#:
#: The TODO app's reminders connect to it from their `AppConfig.ready()` to recompute the
#: instants that depend on the user's local time.
timezone_changed = Signal()

#: Sent, on commit, when a user's email address becomes confirmed: by the emailed confirmation
#: link (`services.email_confirmation.EmailConfirmationService.confirm_token`) and by a password
#: reset through an emailed link, which confirms the address it went to
#: (`services.password_reset.PasswordResetService.reset_password`). Never for an account created
#: already confirmed ("Sign in with Google"/Microsoft). `sender` is `User`; providing args: `user`.
#:
#: `billing` connects to it from its `AppConfig.ready()` to grant the credits of a promo code
#: entered at signup (issue #70).
email_confirmed = Signal()
