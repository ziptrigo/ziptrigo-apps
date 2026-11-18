"""The share-link JWT API (`/api/qr/{id}/links`): every endpoint and status, other users' 404s,
refusals that spend no budget, the 409 of a taken-down code, revoking that is never rate limited,
the new fields of a QR code and the query counts. Over the real URLs, with real tokens."""

import re
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.tokens import CustomAccessToken
from apps.core import ratelimit
from apps.qr_code.limits import MAX_ACTIVE_LINKS_PER_CODE, MAX_RECIPIENTS_PER_LINK, RESEND_COOLDOWN
from apps.qr_code.models import QRShareLink, QRShareRecipient
from apps.qr_code.services import share, takedown
from config.api import TAGS, api

from .share_support import make_link, make_qrcode, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def owner():
    return make_user(name='Alice Owner')


@pytest.fixture
def qrcode(owner):
    return make_qrcode(owner, name='Shop door')


@pytest.fixture
def link(qrcode):
    return share.create_link(qrcode)


def headers(user) -> dict[str, str]:
    return {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(user)}'}


@pytest.fixture
def auth(owner):
    return headers(owner)


@pytest.fixture
def stranger_auth():
    return headers(make_user())


@pytest.fixture
def sent(monkeypatch):
    """Every email the task sends, as the keyword arguments of `send_email`."""
    emails: list[dict] = []
    monkeypatch.setattr(
        'apps.qr_code.services.share_emails.send_email',
        lambda **kwargs: emails.append(kwargs) or (1, 0),
    )
    return emails


def links_url(qrcode, *rest) -> str:
    return '/'.join(['/api/qr', str(qrcode.pk), 'links', *map(str, rest)])


def post(client, url, body=None, **extra):
    return client.post(
        url, body if body is not None else {}, content_type='application/json', **extra
    )


def patch(client, url, body, **extra):
    return client.patch(url, body, content_type='application/json', **extra)


def staff_takedown(qrcode):
    staff = make_user(is_staff=True)
    assert takedown.take_down_qrcode(qrcode, by=staff, reason='Phishing', internal_note='secret')


# -- Wiring ---------------------------------------------------------------------------------------


class TestWiring:
    def share_operations(self):
        schema = api.get_openapi_schema()
        return [
            (method.upper(), path)
            for path, methods in schema['paths'].items()
            if '/links' in path
            for method in methods
            if path.startswith('/api/qr/')
        ]

    def test_every_share_route_needs_a_token(self, client):
        operations = self.share_operations()
        assert len(operations) == 6  # a new endpoint is a deliberate change here
        for method, path in operations:
            url = re.sub(r'\{[a-z_]+\}', str(uuid.uuid4()), path)
            response = client.generic(method, url, data='{}', content_type='application/json')
            assert response.status_code == 401, (method, path)

    def test_the_endpoints_are_tagged_qr_codes(self):
        assert 'QR Codes' in [tag['name'] for tag in TAGS]
        schema = api.get_openapi_schema()
        for path, methods in schema['paths'].items():
            if path.startswith('/api/qr/') and '/links' in path:
                for operation in methods.values():
                    assert operation['tags'] == ['QR Codes'], path

    def test_the_share_schema_names_are_unique_across_the_api(self):
        from ninja import Schema

        seen: dict[str, set[str]] = {}

        def walk(parent):
            for child in parent.__subclasses__():
                if child.__module__.startswith('apps.'):
                    seen.setdefault(child.__name__, set()).add(
                        f'{child.__module__}.{child.__qualname__}'
                    )
                walk(child)

        walk(Schema)
        clashes = {
            name: sorted(where)
            for name, where in seen.items()
            if len(where) > 1 and any('qr_code' in item for item in where)
        }
        assert not clashes, clashes
        assert all(
            name.startswith('QRCodeShare')
            for name in seen
            if '.qr_code.schemas.share' in ''.join(seen[name])
        )

    def test_a_deactivated_account_is_a_401(self, client, qrcode, owner):
        auth = headers(owner)
        owner.status = type(owner).STATUS_INACTIVE
        owner.save()

        assert client.get(links_url(qrcode), **auth).status_code == 401


# -- List and create ------------------------------------------------------------------------------


class TestList:
    def test_lists_active_links_first_with_their_people(self, client, qrcode, owner, auth):
        old = make_link(qrcode, label='old')
        QRShareLink.objects.filter(pk=old.pk).update(revoked_at=timezone.now())
        live = share.create_link(qrcode, label='for Sam')
        QRShareRecipient.objects.create(link=live, email='sam@example.com')

        response = client.get(links_url(qrcode), **auth)

        assert response.status_code == 200
        data = response.json()
        assert [item['label'] for item in data] == ['for Sam', 'old']
        first = data[0]
        assert first['active'] is True and first['revoked_at'] is None
        assert first['url'].endswith(f'/q/{QRShareLink.objects.get(pk=live.pk).code}')
        assert [p['email'] for p in first['recipients']] == ['sam@example.com']
        assert set(first['recipients'][0]) == {'id', 'email', 'last_sent_at'}
        assert set(first) == {
            'id',
            'url',
            'label',
            'expires_at',
            'revoked_at',
            'active',
            'state',
            'recipients',
            'created_at',
        }
        assert data[1]['active'] is False and data[1]['revoked_at'] is not None
        assert [item['state'] for item in data] == ['active', 'revoked']

    def test_the_list_is_three_queries_whatever_the_number_of_links(self, client, qrcode, auth):
        for n in range(MAX_ACTIVE_LINKS_PER_CODE):
            item = share.create_link(qrcode)
            QRShareRecipient.objects.create(link=item, email=f'p{n}@example.com')
        with CaptureQueriesContext(connection) as small:
            client.get(links_url(qrcode), **auth)
        make_link(qrcode, label='extra revoked', revoked_at=timezone.now())
        with CaptureQueriesContext(connection) as large:
            client.get(links_url(qrcode), **auth)

        assert len(small) == len(large) <= 4  # the token's user, the code, the links, the people

    def test_another_users_code_is_a_404(self, client, qrcode, stranger_auth):
        assert client.get(links_url(qrcode), **stranger_auth).status_code == 404

    def test_a_deleted_code_is_a_404(self, client, qrcode, auth):
        qrcode.soft_delete()
        assert client.get(links_url(qrcode), **auth).status_code == 404

    def test_an_unknown_code_is_a_404_and_a_malformed_id_a_422(self, client, auth):
        assert client.get(f'/api/qr/{uuid.uuid4()}/links', **auth).status_code == 404
        assert client.get('/api/qr/nonsense/links', **auth).status_code in (404, 422)

    def test_a_taken_down_code_still_lists_its_links(self, client, qrcode, link, auth):
        staff_takedown(qrcode)
        response = client.get(links_url(qrcode), **auth)

        assert response.status_code == 200 and len(response.json()) == 1


class TestCreate:
    def test_makes_a_link(self, client, qrcode, auth):
        response = post(client, links_url(qrcode), {'label': 'for Sam'}, **auth)

        assert response.status_code == 201
        data = response.json()
        assert data['label'] == 'for Sam' and data['active'] is True
        assert data['recipients'] == [] and data['expires_at'] is None
        stored = QRShareLink.objects.get(pk=data['id'])
        assert data['url'] == share.link_url(stored)

    def test_an_empty_body_is_fine(self, client, qrcode, auth):
        assert post(client, links_url(qrcode), {}, **auth).status_code == 201

    def test_a_bare_date_is_the_end_of_that_day_in_the_owners_zone(
        self, client, qrcode, owner, auth
    ):
        owner.timezone = 'America/Chicago'
        owner.save()
        day = (timezone.now() + timedelta(days=30)).date()

        response = post(client, links_url(qrcode), {'expires_at': day.isoformat()}, **auth)

        assert response.status_code == 201
        stored = QRShareLink.objects.get(pk=response.json()['id'])
        local = stored.expires_at.astimezone(ZoneInfo('America/Chicago'))
        assert (local.date(), local.hour, local.minute, local.second) == (day, 23, 59, 59)

    def test_a_date_time_is_that_instant_and_a_naive_one_is_in_the_owners_zone(
        self, client, qrcode, owner, auth
    ):
        owner.timezone = 'America/Chicago'
        owner.save()
        day = (timezone.now() + timedelta(days=30)).date()

        aware = post(
            client, links_url(qrcode), {'expires_at': f'{day}T18:00:00+00:00'}, **auth
        ).json()
        naive = post(client, links_url(qrcode), {'expires_at': f'{day}T18:00:00'}, **auth).json()

        assert QRShareLink.objects.get(pk=aware['id']).expires_at.hour == 18
        local = QRShareLink.objects.get(pk=naive['id']).expires_at.astimezone(
            ZoneInfo('America/Chicago')
        )
        assert (local.date(), local.hour) == (day, 18)

    def test_a_past_date_is_a_400_naming_the_field(self, client, qrcode, auth):
        response = post(client, links_url(qrcode), {'expires_at': '2020-01-01'}, **auth)

        assert response.status_code == 400
        assert response.json()['detail'] == 'expires_at: Choose a date that has not passed.'
        assert not QRShareLink.objects.exists()

    def test_a_long_label_is_a_400(self, client, qrcode, auth):
        response = post(client, links_url(qrcode), {'label': 'x' * 500}, **auth)

        assert response.status_code == 400 and response.json()['detail'].startswith('label: ')

    def test_the_cap_on_active_links_is_a_400(self, client, qrcode, auth):
        for _ in range(MAX_ACTIVE_LINKS_PER_CODE):
            share.create_link(qrcode)

        response = post(client, links_url(qrcode), {}, **auth)

        assert response.status_code == 400
        assert response.json()['detail'] == (
            f'A QR code can have at most {MAX_ACTIVE_LINKS_PER_CODE} active links.'
        )

    def test_unknown_keys_and_bad_types_are_a_422(self, client, qrcode, auth):
        assert post(client, links_url(qrcode), {'mode': 'edit'}, **auth).status_code == 422
        assert post(client, links_url(qrcode), {'expires_at': 'soon'}, **auth).status_code == 422
        assert post(client, links_url(qrcode), {'label': 5}, **auth).status_code == 422

    def test_another_users_code_is_a_404_and_creates_nothing(self, client, qrcode, stranger_auth):
        assert post(client, links_url(qrcode), {}, **stranger_auth).status_code == 404
        assert not QRShareLink.objects.exists()

    def test_a_deleted_code_is_a_404(self, client, qrcode, auth):
        qrcode.soft_delete()
        assert post(client, links_url(qrcode), {}, **auth).status_code == 404

    def test_a_taken_down_code_is_a_409_and_creates_nothing(self, client, qrcode, auth):
        staff_takedown(qrcode)

        response = post(client, links_url(qrcode), {}, **auth)

        assert response.status_code == 409
        assert response.json()['detail'] == 'This QR code was taken down and cannot be shared.'
        assert not QRShareLink.objects.exists()


# -- Update and revoke ----------------------------------------------------------------------------


class TestUpdate:
    def test_changes_the_label_and_the_expiry(self, client, qrcode, link, auth):
        day = (timezone.now() + timedelta(days=30)).date().isoformat()

        response = patch(
            client, links_url(qrcode, link.pk), {'label': 'now Sam', 'expires_at': day}, **auth
        )

        assert response.status_code == 200
        data = response.json()
        assert data['label'] == 'now Sam' and data['expires_at'] is not None
        link.refresh_from_db()
        assert link.label == 'now Sam' and link.expires_at is not None

    def test_an_explicit_null_clears_both(self, client, qrcode, auth):
        item = share.create_link(
            qrcode, label='x', expires_on=(timezone.now() + timedelta(days=9)).date()
        )

        response = patch(
            client, links_url(qrcode, item.pk), {'label': None, 'expires_at': None}, **auth
        )

        assert response.status_code == 200
        item.refresh_from_db()
        assert item.label == '' and item.expires_at is None

    def test_fields_left_out_are_unchanged(self, client, qrcode, auth):
        item = share.create_link(
            qrcode, label='keep', expires_on=(timezone.now() + timedelta(days=9)).date()
        )
        before = item.expires_at

        patch(client, links_url(qrcode, item.pk), {'label': 'new'}, **auth)

        item.refresh_from_db()
        assert item.label == 'new' and item.expires_at == before

    def test_extending_an_expired_link_makes_it_active_again(self, client, qrcode, auth):
        expired = make_link(qrcode, expires_at=timezone.now() - timedelta(days=1))
        day = (timezone.now() + timedelta(days=5)).date().isoformat()

        response = patch(client, links_url(qrcode, expired.pk), {'expires_at': day}, **auth)

        assert response.status_code == 200 and response.json()['active'] is True

    def test_a_revoked_link_cannot_be_changed(self, client, qrcode, auth):
        revoked = make_link(qrcode, revoked_at=timezone.now())

        response = patch(client, links_url(qrcode, revoked.pk), {'label': 'x'}, **auth)

        assert response.status_code == 400
        assert response.json()['detail'] == 'This link was revoked and can no longer be changed.'

    def test_a_past_date_is_a_400(self, client, qrcode, link, auth):
        response = patch(client, links_url(qrcode, link.pk), {'expires_at': '2020-01-01'}, **auth)

        assert response.status_code == 400

    def test_unknown_keys_are_a_422(self, client, qrcode, link, auth):
        assert patch(client, links_url(qrcode, link.pk), {'code': 'x'}, **auth).status_code == 422

    def test_another_users_code_or_link_is_a_404(self, client, qrcode, link, auth, stranger_auth):
        assert (
            patch(client, links_url(qrcode, link.pk), {'label': 'x'}, **stranger_auth).status_code
            == 404
        )
        other = make_qrcode(qrcode.created_by, name='Other')
        assert patch(client, links_url(other, link.pk), {'label': 'x'}, **auth).status_code == 404
        assert (
            patch(client, links_url(qrcode, uuid.uuid4()), {'label': 'x'}, **auth).status_code
            == 404
        )

    def test_a_taken_down_code_is_a_409(self, client, qrcode, link, auth):
        staff_takedown(qrcode)

        response = patch(client, links_url(qrcode, link.pk), {'label': 'x'}, **auth)

        assert response.status_code == 409


class TestRevoke:
    def test_revokes_and_is_idempotent(self, client, qrcode, link, auth):
        url = links_url(qrcode, link.pk)

        first = client.delete(url, **auth)
        link.refresh_from_db()
        stamped = link.revoked_at
        second = client.delete(url, **auth)

        assert (first.status_code, second.status_code) == (204, 204)
        assert stamped is not None
        link.refresh_from_db()
        assert link.revoked_at == stamped
        assert share.get_available(link.code) is None

    def test_works_on_a_taken_down_code(self, client, qrcode, link, auth):
        staff_takedown(qrcode)

        assert client.delete(links_url(qrcode, link.pk), **auth).status_code == 204

    def test_another_users_link_is_a_404_and_stays_active(
        self, client, qrcode, link, stranger_auth
    ):
        assert client.delete(links_url(qrcode, link.pk), **stranger_auth).status_code == 404
        link.refresh_from_db()
        assert link.revoked_at is None

    def test_a_link_of_another_code_is_a_404(self, client, qrcode, link, owner, auth):
        other = make_qrcode(owner, name='Other')
        assert client.delete(links_url(other, link.pk), **auth).status_code == 404

    def test_a_deleted_code_is_a_404(self, client, qrcode, link, auth):
        qrcode.soft_delete()
        assert client.delete(links_url(qrcode, link.pk), **auth).status_code == 404


# -- Email and resend -----------------------------------------------------------------------------


class TestEmail:
    def test_emails_the_link_and_lists_the_people(
        self, client, qrcode, link, auth, sent, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            response = post(
                client,
                links_url(qrcode, link.pk, 'email'),
                {'emails': ['Ann@Example.com', 'bob@example.com'], 'message': 'Have a look'},
                **auth,
            )

        assert response.status_code == 200
        data = response.json()
        assert sorted(p['email'] for p in data['recipients']) == [
            'Ann@example.com',
            'bob@example.com',
        ]
        assert all(p['last_sent_at'] for p in data['recipients'])
        assert len(sent) == 2
        assert sent[0]['subject'] == 'A QR code was shared with you'
        assert 'Have a look' in sent[0]['text_body']

    def test_a_refusal_is_a_400_naming_the_field(self, client, qrcode, link, auth, sent):
        url = links_url(qrcode, link.pk, 'email')

        bad = post(client, url, {'emails': ['not-an-address']}, **auth)
        long = post(client, url, {'emails': ['a@example.com'], 'message': 'x' * 501}, **auth)
        too_many = post(
            client,
            url,
            {'emails': [f'p{n}@example.com' for n in range(MAX_RECIPIENTS_PER_LINK + 1)]},
            **auth,
        )

        assert bad.status_code == 400
        assert bad.json()['detail'] == 'emails: "not-an-address" is not a valid email address.'
        assert long.status_code == 400 and long.json()['detail'].startswith('message: ')
        assert too_many.status_code == 400 and 'at most 20 people' in too_many.json()['detail']
        assert not QRShareRecipient.objects.exists() and sent == []

    def test_an_over_long_address_is_a_422(self, client, qrcode, link, auth):
        url = links_url(qrcode, link.pk, 'email')
        long = 'a' * 320 + '@example.com'
        assert post(client, url, {'emails': [long]}, **auth).status_code == 422

    def test_an_empty_list_and_unknown_keys_are_a_422(self, client, qrcode, link, auth):
        url = links_url(qrcode, link.pk, 'email')
        assert post(client, url, {'emails': []}, **auth).status_code == 422
        assert post(client, url, {}, **auth).status_code == 422
        assert (
            post(client, url, {'emails': ['a@example.com'], 'cc': 'x'}, **auth).status_code == 422
        )

    def test_an_unconfirmed_owner_is_a_400(self, client, qrcode, link, owner, auth):
        owner.email_confirmed = False
        owner.save()

        response = post(
            client, links_url(qrcode, link.pk, 'email'), {'emails': ['a@example.com']}, **auth
        )

        assert response.status_code == 400
        assert response.json()['detail'] == 'Confirm your email address to send a QR code by email.'

    def test_a_revoked_link_is_a_400(self, client, qrcode, link, auth):
        share.revoke_link(link)

        response = post(
            client, links_url(qrcode, link.pk, 'email'), {'emails': ['a@example.com']}, **auth
        )

        assert response.status_code == 400
        assert response.json()['detail'] == 'This link no longer works.'

    def test_an_address_in_its_cooldown_is_a_400(self, client, qrcode, link, auth, sent):
        QRShareRecipient.objects.create(
            link=link, email='ann@example.com', last_sent_at=timezone.now()
        )

        response = post(
            client, links_url(qrcode, link.pk, 'email'), {'emails': ['ANN@example.com']}, **auth
        )

        assert response.status_code == 400
        assert 'was emailed recently' in response.json()['detail']
        assert sent == []

    def test_a_taken_down_code_is_a_409(self, client, qrcode, link, auth, sent):
        staff_takedown(qrcode)

        response = post(
            client, links_url(qrcode, link.pk, 'email'), {'emails': ['a@example.com']}, **auth
        )

        assert response.status_code == 409
        assert sent == [] and not QRShareRecipient.objects.exists()

    def test_another_users_link_is_a_404(self, client, qrcode, link, stranger_auth):
        url = links_url(qrcode, link.pk, 'email')
        assert post(client, url, {'emails': ['a@example.com']}, **stranger_auth).status_code == 404


class TestResend:
    @pytest.fixture
    def person(self, link):
        return QRShareRecipient.objects.create(
            link=link, email='ann@example.com', last_sent_at=timezone.now() - RESEND_COOLDOWN * 2
        )

    def test_emails_the_person_again(
        self, client, qrcode, link, person, auth, sent, django_capture_on_commit_callbacks
    ):
        url = links_url(qrcode, link.pk, 'recipients', person.pk, 'resend')
        before = person.last_sent_at

        with django_capture_on_commit_callbacks(execute=True):
            response = post(client, url, **auth)

        assert response.status_code == 200
        assert [p['email'] for p in response.json()['recipients']] == ['ann@example.com']
        person.refresh_from_db()
        assert person.last_sent_at > before
        assert len(sent) == 1 and sent[0]['to'] == 'ann@example.com'

    def test_the_cooldown_is_a_400(self, client, qrcode, link, person, auth, sent):
        QRShareRecipient.objects.filter(pk=person.pk).update(last_sent_at=timezone.now())

        response = post(
            client, links_url(qrcode, link.pk, 'recipients', person.pk, 'resend'), **auth
        )

        assert response.status_code == 400
        assert 'This person was emailed recently' in response.json()['detail']
        assert sent == []

    def test_404s(self, client, qrcode, link, person, auth, stranger_auth, owner):
        url = links_url(qrcode, link.pk, 'recipients', person.pk, 'resend')
        assert post(client, url, **stranger_auth).status_code == 404
        assert (
            post(
                client, links_url(qrcode, link.pk, 'recipients', uuid.uuid4(), 'resend'), **auth
            ).status_code
            == 404
        )
        other_link = share.create_link(make_qrcode(owner, name='Other'))
        elsewhere = links_url(qrcode, other_link.pk, 'recipients', person.pk, 'resend')
        assert post(client, elsewhere, **auth).status_code == 404

    def test_a_taken_down_code_is_a_409(self, client, qrcode, link, person, auth, sent):
        staff_takedown(qrcode)

        response = post(
            client, links_url(qrcode, link.pk, 'recipients', person.pk, 'resend'), **auth
        )

        assert response.status_code == 409 and sent == []


# -- Rate limits ----------------------------------------------------------------------------------


class TestRateLimits:
    @pytest.fixture(autouse=True)
    def _limits(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'QR_SHARE_USER': (3, 60),
            'QR_SHARE_EMAIL_USER': (3, 3600),
        }

    def test_creating_is_limited_with_retry_after_and_revoking_never_is(
        self, client, qrcode, owner, auth
    ):
        made = [post(client, links_url(qrcode), {}, **auth) for _ in range(3)]
        limited = post(client, links_url(qrcode), {}, **auth)

        assert [r.status_code for r in made] == [201, 201, 201]
        assert limited.status_code == 429 and int(limited['Retry-After']) > 0
        assert QRShareLink.objects.count() == 3
        for item in QRShareLink.objects.all():
            assert client.delete(links_url(qrcode, item.pk), **auth).status_code == 204
        assert not QRShareLink.objects.filter(revoked_at__isnull=True).exists()

    def test_updating_is_limited_too(self, client, qrcode, link, auth):
        url = links_url(qrcode, link.pk)
        for n in range(3):
            assert patch(client, url, {'label': f'v{n}'}, **auth).status_code == 200

        assert patch(client, url, {'label': 'last'}, **auth).status_code == 429

    def test_refusals_spend_no_budget(self, client, qrcode, link, auth, owner):
        url = links_url(qrcode, link.pk)
        for _ in range(6):  # invalid, taken-down-free refusals and 404s
            assert (
                post(client, links_url(qrcode), {'expires_at': '2020-01-01'}, **auth).status_code
                == 400
            )
            assert patch(client, url, {'expires_at': '2020-01-01'}, **auth).status_code == 400
            assert (
                patch(client, links_url(qrcode, uuid.uuid4()), {'label': 'x'}, **auth).status_code
                == 404
            )
        revoked = make_link(qrcode, revoked_at=timezone.now())
        for _ in range(6):
            assert (
                patch(client, links_url(qrcode, revoked.pk), {'label': 'x'}, **auth).status_code
                == 400
            )

        assert patch(client, url, {'label': 'ok'}, **auth).status_code == 200  # budget untouched

    def test_an_empty_patch_spends_nothing(self, client, qrcode, link, auth):
        url = links_url(qrcode, link.pk)
        for _ in range(10):
            assert patch(client, url, {}, **auth).status_code == 200

        assert post(client, links_url(qrcode), {}, **auth).status_code == 201

    def test_a_patch_that_changes_nothing_spends_nothing(self, client, qrcode, auth):
        day = (timezone.now() + timedelta(days=9)).date()
        dated = share.create_link(qrcode, label='Sam', expires_on=day)
        plain = share.create_link(qrcode)
        before = dated.expires_at
        for _ in range(8):
            same = patch(
                client,
                links_url(qrcode, dated.pk),
                {'label': 'Sam', 'expires_at': day.isoformat()},
                **auth,
            )
            assert same.status_code == 200 and same.json()['label'] == 'Sam'
            cleared = patch(client, links_url(qrcode, plain.pk), {'expires_at': None}, **auth)
            assert cleared.status_code == 200
        dated.refresh_from_db()

        assert dated.expires_at == before
        assert ratelimit.peek_user(qrcode.created_by, 'QR_SHARE_USER').remaining == 3

    def test_a_real_patch_after_no_ops_still_spends_one(self, client, qrcode, link, auth):
        url = links_url(qrcode, link.pk)
        patch(client, url, {'label': ''}, **auth)
        assert patch(client, url, {'label': 'new'}, **auth).status_code == 200
        assert ratelimit.peek_user(qrcode.created_by, 'QR_SHARE_USER').remaining == 2

    def test_a_taken_down_code_spends_no_budget(self, client, qrcode, auth):
        staff_takedown(qrcode)
        for _ in range(6):
            assert post(client, links_url(qrcode), {}, **auth).status_code == 409

        assert ratelimit.peek_user(qrcode.created_by, 'QR_SHARE_USER').remaining == 3

    def test_the_email_budget_is_one_hit_per_email_and_a_429_emails_nobody(
        self, client, qrcode, link, auth, sent, django_capture_on_commit_callbacks
    ):
        url = links_url(qrcode, link.pk, 'email')
        with django_capture_on_commit_callbacks(execute=True):
            ok = post(client, url, {'emails': ['a@example.com', 'b@example.com']}, **auth)
        over = post(client, url, {'emails': ['c@example.com', 'd@example.com']}, **auth)

        assert ok.status_code == 200
        assert over.status_code == 429 and int(over['Retry-After']) > 0
        assert 'try again in' in over.json()['detail']
        assert QRShareRecipient.objects.count() == 2 and len(sent) == 2

    def test_email_refusals_spend_none_of_it(self, client, qrcode, link, auth, sent):
        url = links_url(qrcode, link.pk, 'email')
        for _ in range(6):
            assert post(client, url, {'emails': ['bad']}, **auth).status_code == 400

        assert (
            post(
                client, url, {'emails': ['a@example.com', 'b@example.com', 'c@example.com']}, **auth
            ).status_code
            == 200
        )

    def test_email_and_link_budgets_are_separate(self, client, qrcode, link, auth, sent):
        for _ in range(3):
            post(client, links_url(qrcode), {}, **auth)
        assert post(client, links_url(qrcode), {}, **auth).status_code == 429

        response = post(
            client, links_url(qrcode, link.pk, 'email'), {'emails': ['a@example.com']}, **auth
        )

        assert response.status_code == 200


# -- The QR code's new fields ----------------------------------------------------------------------


class TestQRCodeFields:
    def test_a_plain_code_has_no_link_and_is_not_taken_down(self, client, qrcode, auth):
        data = client.get(f'/api/qr/{qrcode.pk}', **auth).json()

        assert data['has_active_link'] is False
        assert data['taken_down'] is False and data['takedown_reason'] is None

    def test_has_active_link_follows_the_links(self, client, qrcode, link, auth):
        assert client.get(f'/api/qr/{qrcode.pk}', **auth).json()['has_active_link'] is True

        share.revoke_link(link)
        assert client.get(f'/api/qr/{qrcode.pk}', **auth).json()['has_active_link'] is False

        make_link(qrcode, expires_at=timezone.now() - timedelta(days=1))
        assert client.get(f'/api/qr/{qrcode.pk}', **auth).json()['has_active_link'] is False

    def test_a_taken_down_code_shows_the_reason_but_never_the_internal_note(
        self, client, qrcode, link, auth
    ):
        staff_takedown(qrcode)

        response = client.get(f'/api/qr/{qrcode.pk}', **auth)
        listed = client.get('/api/qr/', **auth).json()[0]

        for data in (response.json(), listed):
            assert data['taken_down'] is True and data['takedown_reason'] == 'Phishing'
            assert data['has_active_link'] is False  # its links don't work
        assert 'secret' not in response.text and 'internal' not in response.text

    def test_the_list_flags_each_code_in_one_query(self, client, owner, auth):
        shared = make_qrcode(owner, name='Shared')
        share.create_link(shared)
        make_qrcode(owner, name='Plain')

        data = {item['name']: item for item in client.get('/api/qr/', **auth).json()}

        assert (data['Shared']['has_active_link'], data['Plain']['has_active_link']) == (
            True,
            False,
        )

    def test_the_list_does_not_query_per_row(self, client, owner, auth):
        for n in range(3):
            share.create_link(make_qrcode(owner, name=f'a{n}'))
        with CaptureQueriesContext(connection) as small:
            client.get('/api/qr/', **auth)
        for n in range(10):
            share.create_link(make_qrcode(owner, name=f'b{n}'))
        with CaptureQueriesContext(connection) as large:
            client.get('/api/qr/', **auth)

        assert len(small) == len(large)

    def test_a_rename_keeps_the_flags(self, client, qrcode, link, auth):
        response = patch(client, f'/api/qr/{qrcode.pk}', {'name': 'New'}, **auth)

        assert response.status_code == 200
        assert response.json()['has_active_link'] is True

    def test_a_new_code_has_no_link(self, client, auth):
        response = post(
            client,
            '/api/qr/',
            {'name': 'n', 'qr_type': 'text', 'qr_format': 'png', 'data': 'hello'},
            **auth,
        )

        assert response.status_code == 201
        assert response.json()['has_active_link'] is False
