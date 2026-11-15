"""Who may use a file request's link (issue #306): the access matrix of audience x password x
visitor (`services.request_access`)."""

import pytest
from django.contrib.auth.models import AnonymousUser

from apps.accounts.tests.factories import UserFactory

from ..services import password as password_service
from ..services.request_access import Access, check_access, is_owner

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


class FakeSession(dict):
    """The bits of a session `check_access` reads."""


def _visitors(transfer):
    return {
        'owner': transfer.owner,
        'anonymous': AnonymousUser(),
        'stranger': UserFactory(email='stranger@example.com', email_confirmed=True),
        'invited': UserFactory(email='Invited@Example.com', email_confirmed=True),
        'invited_unconfirmed': UserFactory(email='waiting@example.com', email_confirmed=False),
    }


#: audience -> visitor -> the access once any password is out of the way.
_AUDIENCE = {
    'anyone': {
        'owner': Access.OK,
        'anonymous': Access.OK,
        'stranger': Access.OK,
        'invited': Access.OK,
        'invited_unconfirmed': Access.OK,
    },
    'signed_in': {
        'owner': Access.OK,
        'anonymous': Access.NEEDS_SIGN_IN,
        'stranger': Access.OK,
        'invited': Access.OK,
        'invited_unconfirmed': Access.OK,
    },
    'selected': {
        'owner': Access.OK,
        'anonymous': Access.NEEDS_SIGN_IN,
        'stranger': Access.NOT_INVITED,
        'invited': Access.OK,
        'invited_unconfirmed': Access.NEEDS_CONFIRMED_EMAIL,
    },
}


@pytest.mark.parametrize('audience', ['anyone', 'signed_in', 'selected'])
@pytest.mark.parametrize(
    'visitor', ['owner', 'anonymous', 'stranger', 'invited', 'invited_unconfirmed']
)
@pytest.mark.parametrize('password', ['none', 'locked', 'unlocked'])
def test_access_matrix(make_request, audience, visitor, password):
    transfer = make_request(
        upload_audience=audience,
        emails=['invited@example.com', 'WAITING@example.com'] if audience == 'selected' else [],
        password='' if password == 'none' else 'sekret',
    )
    session = FakeSession()
    if password == 'unlocked':
        password_service.unlock_in_session(session, transfer.id, transfer.password_hash)

    expected = _AUDIENCE[audience][visitor]
    if password == 'locked' and visitor != 'owner':
        # The password comes first, whatever the audience says.
        expected = Access.NEEDS_PASSWORD

    assert check_access(transfer, _visitors(transfer)[visitor], session) == expected


def test_the_owner_is_never_asked_for_the_password(make_request):
    transfer = make_request(password='sekret', upload_audience='selected', emails=['a@x.org'])
    assert check_access(transfer, transfer.owner, FakeSession()) == Access.OK


def test_a_changed_password_locks_the_session_again(make_request):
    transfer = make_request(password='sekret')
    session = FakeSession()
    password_service.unlock_in_session(session, transfer.id, transfer.password_hash)
    assert check_access(transfer, AnonymousUser(), session) == Access.OK

    transfer.password_hash = password_service.hash_password('another')
    assert check_access(transfer, AnonymousUser(), session) == Access.NEEDS_PASSWORD


def test_an_invite_matches_ignoring_case(make_request):
    transfer = make_request(upload_audience='selected', emails=['Ann@Example.com'])
    account = UserFactory(email='ANN@example.com', email_confirmed=True)
    assert check_access(transfer, account, FakeSession()) == Access.OK


def test_removing_an_invitee_stops_their_access(make_request):
    transfer = make_request(upload_audience='selected', emails=['ann@example.com', 'b@example.com'])
    account = UserFactory(email='ann@example.com', email_confirmed=True)
    assert check_access(transfer, account, FakeSession()) == Access.OK
    transfer.invites.filter(email='ann@example.com').delete()
    assert check_access(transfer, account, FakeSession()) == Access.NOT_INVITED


def test_is_owner(make_request):
    transfer = make_request()
    assert is_owner(transfer, transfer.owner)
    assert not is_owner(transfer, AnonymousUser())
    assert not is_owner(transfer, UserFactory())
    assert not is_owner(transfer, None)
