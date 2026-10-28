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
