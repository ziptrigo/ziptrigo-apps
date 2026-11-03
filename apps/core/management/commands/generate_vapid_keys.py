"""Print a new VAPID key pair for Web Push (issue #239).

The two lines go into an environment's env file (`.env.local`, `.env.staging`, `.env.prod`) with a
`VAPID_SUBJECT=mailto:...` of your own. Nothing is written anywhere: run it once per environment
and keep the private key out of the repository. Changing the pair later makes every browser
subscribe again.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Generate a VAPID (P-256) key pair and print it as env file lines."""

    help = 'Prints a new VAPID key pair (VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY) for Web Push.'

    def handle(self, *args: object, **options: object) -> None:
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid
        from py_vapid.utils import b64urlencode

        vapid = Vapid()
        vapid.generate_keys()
        private_raw = vapid.private_key.private_numbers().private_value.to_bytes(32, 'big')
        public_raw = vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self.stdout.write(f'VAPID_PUBLIC_KEY={b64urlencode(public_raw)}')
        self.stdout.write(f'VAPID_PRIVATE_KEY={b64urlencode(private_raw)}')
