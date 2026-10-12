"""Link receipts that have a vendor name but no vendor.

    ./manage.py receipts_link_vendors

The `receipts.link_vendors` job does the same every hour. Run this by hand right after the deploy
that introduced vendors, to catch receipts a container of the previous release processed after the
migration's backfill. It only fills in the link (to the user's vendor called that, else a new
unvetted one) and never changes a receipt's vendor name; running it again is harmless.
"""

from typing import Any

from django.core.management.base import BaseCommand

from apps.receipts.services.vendors import link_unlinked_receipts, users_with_unlinked_receipts


class Command(BaseCommand):
    help = 'Link receipts that have a vendor name but no vendor (idempotent).'

    def handle(self, *args: Any, **options: Any) -> None:
        users = users_with_unlinked_receipts()
        linked = sum(link_unlinked_receipts(user_id) for user_id in users)
        self.stdout.write(
            self.style.SUCCESS(f'Linked {linked} receipt(s) of {len(users)} user(s) to a vendor.')
        )
