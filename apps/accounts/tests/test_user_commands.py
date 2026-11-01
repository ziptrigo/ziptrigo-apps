"""`manage.py create_user` and `manage.py confirm_email`, behind `inv users create|confirm`."""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import User
from apps.accounts.signals import email_confirmed
from apps.accounts.validators import PasswordValidator

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

PASSWORD = 'Sup3r-secret'


@pytest.fixture(autouse=True)
def _password_in_env(monkeypatch):
    monkeypatch.setenv('USERS_PASSWORD', PASSWORD)


def create(*args) -> str:
    out = StringIO()
    call_command('create_user', *args, stdout=out)
    return out.getvalue()


def confirm(email: str) -> str:
    out = StringIO()
    call_command('confirm_email', email, stdout=out)
    return out.getvalue()


class TestCreateUser:
    def test_creates_an_ordinary_user_with_the_password_and_an_unconfirmed_email(self):
        out = create('new@example.com')

        user = User.objects.get(email='new@example.com')
        assert user.check_password(PASSWORD)
        assert not user.is_staff and not user.is_superuser
        assert not user.email_confirmed and user.email_confirmed_at is None
        assert user.status == User.STATUS_ACTIVE
        assert 'Created user new@example.com (email not confirmed)' in out

    def test_superuser_is_staff_and_superuser(self):
        out = create('root@example.com', '--superuser')

        user = User.objects.get(email='root@example.com')
        assert user.is_superuser and user.is_staff
        assert 'superuser' in out

    def test_staff_is_staff_but_not_a_superuser(self):
        out = create('staff@example.com', '--staff')

        user = User.objects.get(email='staff@example.com')
        assert user.is_staff and not user.is_superuser
        assert 'staff user' in out

    def test_superuser_and_staff_are_mutually_exclusive(self):
        with pytest.raises(CommandError):
            create('x@example.com', '--superuser', '--staff')
        assert not User.objects.filter(email='x@example.com').exists()

    def test_the_same_check_holds_when_called_with_keywords(self):
        with pytest.raises(CommandError, match='cannot be used together'):
            call_command('create_user', 'x@example.com', superuser=True, staff=True)

    def test_confirm_confirms_the_email_at_creation(self):
        out = create('ok@example.com', '--confirm')

        user = User.objects.get(email='ok@example.com')
        assert user.email_confirmed and user.email_confirmed_at is not None
        assert 'confirmed' in out and 'not confirmed' not in out

    def test_confirm_tells_the_apps_that_care(self, django_capture_on_commit_callbacks):
        seen = []

        def receiver(sender, user, **kwargs):
            seen.append(user.email)

        email_confirmed.connect(receiver, weak=False)
        try:
            with django_capture_on_commit_callbacks(execute=True):
                create('signal@example.com', '--confirm')
                create('quiet@example.com')
        finally:
            email_confirmed.disconnect(receiver)

        assert seen == ['signal@example.com']

    def test_a_taken_address_is_refused_in_any_case(self):
        create('taken@example.com')

        with pytest.raises(CommandError, match='already exists'):
            create('Taken@Example.com')
        assert User.objects.filter(email__iexact='taken@example.com').count() == 1

    def test_the_domain_is_normalised_like_everywhere_else(self):
        create('Someone@EXAMPLE.COM')

        assert User.objects.filter(email='Someone@example.com').exists()

    @pytest.mark.parametrize('bad', ['', 'not-an-email', 'a@', '@b.com'])
    def test_an_invalid_address_is_refused(self, bad):
        with pytest.raises(CommandError, match='not a valid email'):
            create(bad)

    @pytest.mark.parametrize('weak', ['ab1', 'nodigitshere', '12345'])
    def test_a_weak_password_is_refused_and_nothing_is_created(self, monkeypatch, weak):
        monkeypatch.setenv('USERS_PASSWORD', weak)

        with pytest.raises(CommandError):
            create('weak@example.com')
        assert not User.objects.filter(email='weak@example.com').exists()

    def test_the_password_rules_are_the_sign_up_ones(self):
        assert PasswordValidator().get_help_text() == (
            'Password must be 6 to 128 characters long and contain at least one digit.'
        )

    def test_without_a_password_and_without_a_terminal_it_says_so(self, monkeypatch):
        monkeypatch.delenv('USERS_PASSWORD')
        monkeypatch.setattr('sys.stdin', StringIO(''))

        with pytest.raises(CommandError, match='USERS_PASSWORD'):
            create('nopass@example.com')

    def test_it_asks_for_the_password_in_a_terminal(self, monkeypatch):
        monkeypatch.delenv('USERS_PASSWORD')
        monkeypatch.setattr('sys.stdin.isatty', lambda: True, raising=False)
        monkeypatch.setattr('getpass.getpass', lambda prompt='': 'Typed-pass1')

        create('typed@example.com')

        assert User.objects.get(email='typed@example.com').check_password('Typed-pass1')

    def test_the_password_is_never_printed(self):
        out = create('quiet@example.com', '--confirm')

        assert PASSWORD not in out

    def test_a_failure_while_confirming_leaves_no_user_behind(self, monkeypatch):
        def boom(user):
            raise RuntimeError('boom')

        monkeypatch.setattr('apps.accounts.management.commands.create_user.confirm_user', boom)

        with pytest.raises(RuntimeError):
            create('atomic@example.com', '--confirm')
        assert not User.objects.filter(email='atomic@example.com').exists()


class TestConfirmEmail:
    def make(self, email='someone@example.com', **fields):
        return User.objects.create_user(email, PASSWORD, **fields)

    def test_confirms_an_unconfirmed_user(self):
        user = self.make()

        out = confirm('someone@example.com')

        user.refresh_from_db()
        assert user.email_confirmed and user.email_confirmed_at is not None
        assert 'Confirmed someone@example.com' in out

    def test_it_can_sign_in_by_password_afterwards(self, client):
        self.make()
        confirm('someone@example.com')

        response = client.post(
            '/account/login/',
            {'email': 'someone@example.com', 'password': PASSWORD},
            HTTP_HX_REQUEST='true',
        )

        assert response.status_code in (200, 204)
        assert '_auth_user_id' in client.session

    def test_an_already_confirmed_user_is_left_alone_and_nobody_is_told_twice(self):
        user = self.make(email_confirmed=True)
        before = user.email_confirmed_at
        seen = []

        def receiver(sender, user, **kwargs):
            seen.append(user.pk)

        email_confirmed.connect(receiver, weak=False)
        try:
            out = confirm('someone@example.com')
        finally:
            email_confirmed.disconnect(receiver)

        user.refresh_from_db()
        assert user.email_confirmed_at == before
        assert seen == []
        assert 'already confirmed' in out

    def test_confirming_tells_the_apps_that_care_once(self, django_capture_on_commit_callbacks):
        self.make()
        seen = []

        def receiver(sender, user, **kwargs):
            seen.append(user.email)

        email_confirmed.connect(receiver, weak=False)
        try:
            with django_capture_on_commit_callbacks(execute=True):
                confirm('someone@example.com')
                confirm('someone@example.com')
        finally:
            email_confirmed.disconnect(receiver)

        assert seen == ['someone@example.com']

    def test_the_address_is_found_in_any_case(self):
        user = self.make('Mixed@example.com')

        confirm('mixed@EXAMPLE.com')

        user.refresh_from_db()
        assert user.email_confirmed

    def test_an_unknown_address_is_an_error(self):
        with pytest.raises(CommandError, match='No user has the email'):
            confirm('ghost@example.com')

    def test_two_legacy_spellings_need_the_exact_one(self):
        first = self.make('Dup@example.com')
        second = self.make('dup@example.com')

        with pytest.raises(CommandError, match='different cases'):
            confirm('DUP@example.com')
        confirm('dup@example.com')

        first.refresh_from_db()
        second.refresh_from_db()
        assert second.email_confirmed and not first.email_confirmed

    def test_a_confirmation_link_still_out_for_the_address_is_retired(self, monkeypatch):
        user = self.make()
        retired = []
        service = type(
            'S', (), {'invalidate_pending_for_email': lambda self, email: retired.append(email)}
        )()
        monkeypatch.setattr(
            'apps.accounts.management.commands.confirm_email.get_email_confirmation_service',
            lambda: service,
        )

        confirm(user.email)

        assert retired == [user.email]


def test_confirm_user_is_what_the_confirmation_link_uses():
    """One function for the emailed link and for the operator, so the side effects can't drift."""
    from apps.accounts.services import email_confirmation

    user = User.objects.create_user('link@example.com', PASSWORD)

    assert email_confirmation.confirm_user(user) is True
    assert email_confirmation.confirm_user(user) is False
    user.refresh_from_db()
    assert user.email_confirmed
