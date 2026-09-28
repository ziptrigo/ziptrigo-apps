"""Signals `billing` emits so a product can react to a balance change without `billing` importing
that product (see the dependency layering in `CLAUDE.md`: only products may import `billing`, never
the other way).
"""

from django.dispatch import Signal

#: Sent by `apps.billing.services.credits.add_credits` after credits are added to a user's
#: balance. Providing args: `user` (the `accounts.User` whose balance changed) and `amount` (the
#: number of credits added, always > 0).
#:
#: `file_transfer` listens for this to re-enable that user's suspended transfers once they've
#: topped up; see `apps/file_transfer/apps.py`.
credits_added = Signal()
