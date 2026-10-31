"""Confirm a user's email address by hand: `python manage.py confirm_email <email>`, or
`inv users confirm <env> <email>`.

Does what following the emailed confirmation link does (`email_confirmed` is set, the apps that care
are told, which is when billing grants a promo code's credits) without the email. Any confirmation
link still out for the address is retired. Confirming an already confirmed account changes nothing.
"""

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts.models import User
from apps.accounts.services.email_confirmation import (
    confirm_user,
    get_email_confirmation_service,
)


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('email')

    def handle(self, *args, **options) -> None:
        email = User.objects.normalize_email((options['email'] or '').strip())
        matches = list(User.objects.filter(email__iexact=email))
        # Legacy rows can differ only by case (CLAUDE.md, "Email case"): prefer the exact spelling.
        exact = [user for user in matches if user.email == email]
        if exact:
            matches = exact
        if not matches:
            raise CommandError(f'No user has the email {email}.')
        if len(matches) > 1:
            raise CommandError(
                f'{len(matches)} users have the email {email} in different cases; '
                'type the address exactly as it is stored.'
            )

        user = matches[0]
        if not confirm_user(user):
            self.stdout.write(f'{user.email} was already confirmed.')
            return
        get_email_confirmation_service().invalidate_pending_for_email(user.email)
        self.stdout.write(self.style.SUCCESS(f'Confirmed {user.email}.'))
