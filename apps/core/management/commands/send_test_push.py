"""Send the Web Push test notification to a user's devices (issue #239).

    ./manage.py send_test_push me@example.com

A "Yes / No" question: tapping a button stores the answer (admin: Push answers), tapping the
notification opens the account page. The user must have turned notifications on, on the account
page, on at least one device.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.core.services import push


class Command(BaseCommand):
    """Send the test notification to every device the user subscribed."""

    help = 'Sends a test push notification (Yes / No) to every device of the user with that email.'

    def add_arguments(self, parser) -> None:
        parser.add_argument('email', help="The user's email address.")

    def handle(self, *args: object, **options: object) -> None:
        if not push.is_configured():
            raise CommandError(
                'Push is not configured: set VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY and VAPID_SUBJECT.'
            )
        email = str(options['email']).strip()
        users = get_user_model().objects
        user = users.filter(email=email).first() or users.filter(email__iexact=email).first()
        if user is None:
            raise CommandError(f'No user with the email {email}.')
        devices = user.push_subscriptions.count()
        if not devices:
            raise CommandError(
                f'{email} has no device with notifications on (account page, "Notifications on '
                'this device").'
            )
        result = push.send_test_push(user)
        self.stdout.write(
            f'Sent to {devices} device(s): {result.sent} accepted, {result.removed} gone '
            f'(removed), {result.failed} failed. Push id: {result.push_id}'
        )
        if not result.sent:
            raise CommandError('No device accepted the notification.')
