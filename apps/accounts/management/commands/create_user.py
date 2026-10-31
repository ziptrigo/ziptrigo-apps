"""Create a user from the command line: `python manage.py create_user <email>`, or, with the
environment chosen for you, `inv users create <env> <email> [<password>]`.

Unlike Django's `createsuperuser`, it applies the same rules the sign-up page does (an address that
differs only in case from an existing one is refused, the password passes `PasswordValidator`), can
make a *staff* user as well as a superuser, and can confirm the email in the same step (sign-in by
password needs a confirmed email; `createsuperuser` leaves it unconfirmed).

The password is read from the `USERS_PASSWORD` environment variable, or typed with the echo hidden
when there is a terminal: never an argument here, so it never shows up in `ps`. It is never printed.
"""

import getpass
import os
import sys

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.core.validators import validate_email
from django.db import transaction

from apps.accounts.models import User
from apps.accounts.services.email_confirmation import confirm_user
from apps.accounts.validators import PasswordValidator

PASSWORD_ENV_VAR = 'USERS_PASSWORD'


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('email')
        kind = parser.add_mutually_exclusive_group()
        kind.add_argument('--superuser', action='store_true', help='A superuser (also staff).')
        kind.add_argument('--staff', action='store_true', help='A staff user, not a superuser.')
        parser.add_argument(
            '--confirm', action='store_true', help="Confirm the user's email after creating it."
        )

    def handle(self, *args, **options) -> None:
        email = User.objects.normalize_email((options['email'] or '').strip())
        superuser, staff, confirm = options['superuser'], options['staff'], options['confirm']
        if superuser and staff:  # argparse refuses this too; kept for `call_command`
            raise CommandError('--superuser and --staff cannot be used together.')

        try:
            validate_email(email)
        except ValidationError:
            raise CommandError(f'"{email}" is not a valid email address.') from None
        if User.objects.filter(email__iexact=email).exists():
            raise CommandError(f'A user with the email {email} already exists.')

        password = self._password()
        try:
            PasswordValidator().validate(password)
        except ValidationError as error:
            raise CommandError(' '.join(error.messages)) from None

        with transaction.atomic():
            if superuser:
                user = User.objects.create_superuser(email, password)
            else:
                user = User.objects.create_user(email, password, is_staff=staff)
            if confirm:
                confirm_user(user)

        kind = 'superuser' if superuser else 'staff user' if staff else 'user'
        confirmed = 'confirmed' if user.email_confirmed else 'email not confirmed'
        self.stdout.write(self.style.SUCCESS(f'Created {kind} {user.email} ({confirmed}).'))

    @staticmethod
    def _password() -> str:
        password = os.environ.get(PASSWORD_ENV_VAR)
        if password:
            return password
        if sys.stdin.isatty():
            return getpass.getpass('Password: ')
        raise CommandError(f'No password: set {PASSWORD_ENV_VAR}, or run it in a terminal.')
