"""How the Short URLs API handles credits (`credits_spent`, `expected_cost`, 402, 409) and the
shared rate limits (429), plus the QR image and the preview."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.billing.models import CreditTransaction

from ..models import LinkStatus, ShortLink
from .api_support import (  # noqa: F401 -- the fixtures are used by name
    auth,
    get,
    limit_rule,
    other_auth,
    patch,
    post,
)
from .conftest import balance
from .factories import create_link

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def create(client, auth, **fields):
    return post(
        client, '/links', auth, {'destination': 'https://example.com/p', 'title': 'T', **fields}
    )


class TestCreditsSpent:
    def test_the_alias_the_password_and_an_extra_link_add_up(self, client, auth, funded_user):
        create_link(funded_user)
        create_link(funded_user)  # the two free active links

        response = create(client, auth, alias='allthree', password='pw')

        assert response.status_code == 201
        assert response.json()['credits_spent'] == 3
        assert balance(funded_user) == 7
        link = ShortLink.objects.get(code='allthree')
        assert link.alias_paid_at and link.password_paid_at and link.extra_link_paid_at

    def test_a_price_of_zero_is_not_charged_and_reports_zero(
        self, client, auth, funded_user, prices
    ):
        prices.custom_alias_price = 0
        prices.save()

        response = create(client, auth, alias='freebie')

        assert response.json()['credits_spent'] == 0
        assert balance(funded_user) == 10

    def test_the_ledger_names_the_source(self, client, auth, funded_user):
        create(client, auth, alias='ledger')

        entry = CreditTransaction.objects.filter(user=funded_user, amount=-1).get()
        assert entry.source == 'short_url'

    def test_setting_a_password_charges_once_and_reports_it(self, client, auth, funded_user):
        link = create_link(funded_user)

        first = patch(client, f'/links/{link.pk}', auth, {'password': 'one'})
        second = patch(client, f'/links/{link.pk}', auth, {'password': 'two'})

        assert first.json()['credits_spent'] == 1
        assert second.json()['credits_spent'] == 0
        assert balance(funded_user) == 9

    def test_an_edit_that_charges_nothing_reports_zero(self, client, auth, funded_user):
        link = create_link(funded_user)

        assert patch(client, f'/links/{link.pk}', auth, {'title': 'x'}).json()['credits_spent'] == 0

    def test_enabling_beyond_the_allowance_charges_once_per_link(self, client, auth, funded_user):
        create_link(funded_user)
        create_link(funded_user)
        extra = create_link(funded_user, status=LinkStatus.DISABLED)

        first = post(client, f'/links/{extra.pk}/enable', auth)
        post(client, f'/links/{extra.pk}/disable', auth)
        again = post(client, f'/links/{extra.pk}/enable', auth)

        assert first.json()['credits_spent'] == 1
        assert again.json()['credits_spent'] == 0
        assert balance(funded_user) == 9

    def test_analytics_charge_once(self, client, auth, funded_user):
        link = create_link(funded_user)

        first = post(client, f'/links/{link.pk}/analytics', auth)
        again = post(client, f'/links/{link.pk}/analytics', auth)

        assert first.status_code == 200
        assert first.json()['credits_spent'] == 1 and first.json()['analytics_enabled'] is True
        assert again.json()['credits_spent'] == 0
        assert balance(funded_user) == 9

    def test_a_read_after_a_write_reports_zero(self, client, auth, funded_user):
        link = create_link(funded_user)
        post(client, f'/links/{link.pk}/analytics', auth)

        assert get(client, f'/links/{link.pk}', auth).json()['credits_spent'] == 0


class TestInsufficientCredits:
    def test_a_create_with_an_alias_is_a_402_and_leaves_nothing(
        self, client, other_auth, other_user
    ):
        response = create(client, other_auth, alias='nofunds')

        assert response.status_code == 402
        assert not ShortLink.objects.filter(owner=other_user).exists()
        assert balance(other_user) == 0

    def test_a_free_create_needs_no_credits(self, client, other_auth):
        assert create(client, other_auth).status_code == 201

    def test_a_password_without_credits_is_a_402_and_changes_nothing(
        self, client, other_auth, other_user
    ):
        link = create_link(other_user)

        response = patch(client, f'/links/{link.pk}', other_auth, {'password': 'pw', 'title': 'x'})

        assert response.status_code == 402
        link.refresh_from_db()
        assert link.password_hash is None and link.title == ''

    def test_enabling_an_extra_link_without_credits_is_a_402(
        self, client, other_auth, other_user, prices
    ):
        create_link(other_user)
        create_link(other_user)
        extra = create_link(other_user, status=LinkStatus.DISABLED)

        response = post(client, f'/links/{extra.pk}/enable', other_auth)

        assert response.status_code == 402
        extra.refresh_from_db()
        assert extra.status == LinkStatus.DISABLED and extra.extra_link_paid_at is None

    def test_analytics_without_credits_is_a_402(self, client, other_auth, other_user):
        link = create_link(other_user)

        assert post(client, f'/links/{link.pk}/analytics', other_auth).status_code == 402


class TestExpectedCost:
    def test_the_right_amount_goes_through(self, client, auth, funded_user):
        response = create(client, auth, alias='confirmed', expected_cost=1)

        assert response.status_code == 201
        assert balance(funded_user) == 9

    def test_a_free_create_may_say_zero(self, client, auth):
        assert create(client, auth, expected_cost=0).status_code == 201

    def test_the_wrong_amount_is_a_409_that_charges_and_creates_nothing(
        self, client, auth, funded_user
    ):
        response = create(client, auth, alias='unconfirmed', expected_cost=0)

        assert response.status_code == 409
        assert 'it is now 1 credit' in response.json()['detail']
        assert 'expected_cost=1' in response.json()['detail']
        assert balance(funded_user) == 10
        assert not ShortLink.objects.exists()

    def test_a_free_allowance_used_up_elsewhere_is_caught(self, client, auth, funded_user):
        # The client saw a free create (quote 0), then two links appeared before it sent it.
        quote = post(client, '/links/quote', auth, {'destination': 'https://x.example'}).json()
        create_link(funded_user)
        create_link(funded_user)

        response = create(client, auth, expected_cost=quote['credits'])

        assert response.status_code == 409
        assert balance(funded_user) == 10

    def test_patch(self, client, auth, funded_user):
        link = create_link(funded_user)

        wrong = patch(client, f'/links/{link.pk}', auth, {'password': 'pw', 'expected_cost': 0})
        right = patch(client, f'/links/{link.pk}', auth, {'password': 'pw', 'expected_cost': 1})

        assert wrong.status_code == 409 and right.status_code == 200
        assert balance(funded_user) == 9

    def test_enable_and_analytics_take_it_as_a_query_parameter(self, client, auth, funded_user):
        create_link(funded_user)
        create_link(funded_user)
        extra = create_link(funded_user, status=LinkStatus.DISABLED)

        assert post(client, f'/links/{extra.pk}/enable', auth, expected_cost=0).status_code == 409
        assert post(client, f'/links/{extra.pk}/enable', auth, expected_cost=1).status_code == 200
        assert (
            post(client, f'/links/{extra.pk}/analytics', auth, expected_cost=0).status_code == 409
        )
        assert (
            post(client, f'/links/{extra.pk}/analytics', auth, expected_cost=1).status_code == 200
        )
        assert balance(funded_user) == 8

    def test_the_cost_a_get_reports_is_the_one_that_confirms(self, client, auth, funded_user):
        create_link(funded_user)
        create_link(funded_user)
        extra = create_link(funded_user, status=LinkStatus.DISABLED)
        costs = get(client, f'/links/{extra.pk}', auth).json()['costs']

        response = post(client, f'/links/{extra.pk}/enable', auth, expected_cost=costs['enable'])

        assert response.status_code == 200


class TestRateLimits:
    def test_create_shares_su_create_user_and_says_when_to_retry(
        self, client, auth, settings, ratelimit_clock
    ):
        limit_rule(settings, 'SU_CREATE_USER', limit=1)

        first = create(client, auth)
        second = create(client, auth)

        assert first.status_code == 201
        assert second.status_code == 429
        assert second['Retry-After'] == '2600'
        assert ShortLink.objects.count() == 1

    def test_a_refused_create_costs_no_budget(self, client, auth, settings, ratelimit_clock):
        limit_rule(settings, 'SU_CREATE_USER', limit=1)

        bad = post(client, '/links', auth, {'destination': 'ftp://nope.example'})
        good = create(client, auth)

        assert bad.status_code == 400
        assert good.status_code == 201

    def test_patch_shares_su_edit_user(self, client, auth, funded_user, settings, ratelimit_clock):
        link = create_link(funded_user)
        limit_rule(settings, 'SU_EDIT_USER', limit=1)

        first = patch(client, f'/links/{link.pk}', auth, {'title': 'a'})
        second = patch(client, f'/links/{link.pk}', auth, {'title': 'b'})
        empty = patch(client, f'/links/{link.pk}', auth, {})

        assert (first.status_code, second.status_code) == (200, 429)
        assert empty.status_code == 200  # nothing to do: no budget needed
        link.refresh_from_db()
        assert link.title == 'a'

    def test_the_qr_image_shares_su_qr_user(
        self, client, auth, funded_user, settings, ratelimit_clock
    ):
        link = create_link(funded_user)
        limit_rule(settings, 'SU_QR_USER', limit=1)

        assert get(client, f'/links/{link.pk}/qr.png', auth).status_code == 200
        assert get(client, f'/links/{link.pk}/qr.png', auth).status_code == 429

    def test_the_budget_is_per_user(self, client, auth, other_auth, settings, ratelimit_clock):
        limit_rule(settings, 'SU_CREATE_USER', limit=1)

        assert create(client, auth).status_code == 201
        assert create(client, other_auth).status_code == 201


class TestQrAndPreview:
    def test_the_qr_image_is_a_png_of_the_short_link(self, client, auth, funded_user):
        link = create_link(funded_user, code='Qr00001')

        response = get(client, f'/links/{link.pk}/qr.png', auth)

        assert response.status_code == 200
        assert response['Content-Type'] == 'image/png'
        assert response.content.startswith(b'\x89PNG')
        assert response['Cache-Control'] == 'private, max-age=3600'

    def test_the_qr_image_of_another_users_link_is_a_404(self, client, auth, other_user):
        link = create_link(other_user)

        assert get(client, f'/links/{link.pk}/qr.png', auth).status_code == 404

    def test_the_preview_shows_what_the_public_page_shows(self, client, auth, funded_user):
        link = create_link(
            funded_user, code='Pre0001', destination='https://Shop.example/a?b=1', title='Shop'
        )

        response = get(client, f'/links/{link.pk}/preview', auth)

        assert response.status_code == 200
        assert response.json() == {
            'short_url': 'https://app.example.test/s/Pre0001',
            'preview_url': 'https://app.example.test/s/Pre0001+',
            'destination': 'https://Shop.example/a?b=1',
            'host': 'shop.example',
            'title': 'Shop',
            'created_at': response.json()['created_at'],
            'password_protected': False,
            'available': True,
        }

    def test_the_owner_sees_the_destination_of_a_protected_link_and_whether_it_works(
        self, client, auth, funded_user
    ):
        link = create_link(
            funded_user,
            status=LinkStatus.DISABLED,
            password_hash='pbkdf2_sha256$x',
            expires_at=timezone.now() + timedelta(days=1),
        )

        body = get(client, f'/links/{link.pk}/preview', auth).json()

        assert body['password_protected'] is True
        assert body['destination'] == link.destination
        assert body['available'] is False

    def test_the_preview_of_another_users_link_is_a_404(self, client, auth, other_user):
        link = create_link(other_user)

        assert get(client, f'/links/{link.pk}/preview', auth).status_code == 404
