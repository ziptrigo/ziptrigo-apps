"""The admin's Promo codes section (issue #70)."""

import csv
import io
from datetime import timedelta
from typing import cast

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.billing.admin_promo import BulkCreateForm, PromoCodeAdminForm
from apps.billing.models import PromoCode, PromoRedemption, PromoRedemptionStatus
from apps.billing.services import promo

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

CHANGELIST = reverse('custom_admin:billing_promocode_changelist')
ADD = reverse('custom_admin:billing_promocode_add')
BULK = reverse('custom_admin:billing_promocode_bulk')
EXPORT = reverse('custom_admin:billing_promocode_export')


def _change(code):
    return reverse('custom_admin:billing_promocode_change', args=[code.pk])


def _user(**fields) -> User:
    return cast(User, UserFactory(**fields))


def _confirmed():
    user = _user()
    user.email_confirmed = True
    user.save(update_fields=['email_confirmed'])
    return user


def _add_payload(**overrides):
    return {
        'code': 'LAUNCH2026',
        'effect': 'credits',
        'credits': 100,
        'campaign': 'Launch',
        'active': 'on',
        'starts_at_0': '',
        'starts_at_1': '',
        'ends_at_0': '',
        'ends_at_1': '',
        'max_redemptions': '',
        'new_accounts_within_days': '',
        'allowed_emails': '',
        'notes': '',
        'redemptions-TOTAL_FORMS': '0',
        'redemptions-INITIAL_FORMS': '0',
        **overrides,
    }


def test_add_code_stamps_created_by_and_normalises(client, admin_user):
    client.force_login(admin_user)

    response = client.post(
        ADD, _add_payload(code=' launch2026 ', allowed_emails='A@x.com\nb@x.com, a@x.com')
    )

    assert response.status_code == 302, response.content.decode()[:2000]
    code = PromoCode.objects.get()
    assert code.code == 'LAUNCH2026'
    assert code.created_by == admin_user
    assert code.allowed_emails == ['a@x.com', 'b@x.com']


def test_blank_code_is_generated(client, admin_user):
    client.force_login(admin_user)
    response = client.post(ADD, _add_payload(code=''))
    assert response.status_code == 302
    code = PromoCode.objects.get()
    assert len(code.code) == promo.GENERATED_LENGTH


def test_form_refuses_bad_code_and_dates_and_emails():
    form = PromoCodeAdminForm(data=_add_payload(code='no spaces!'))
    assert not form.is_valid()
    assert 'code' in form.errors

    # The plain form has one `starts_at` input; the admin's split date/time widget posts
    # `starts_at_0`/`_1`, which the view tests use.
    form = PromoCodeAdminForm(
        data=_add_payload(starts_at='2026-11-01 10:00:00', ends_at='2026-11-01 09:00:00')
    )
    assert not form.is_valid()
    assert 'ends_at' in form.errors

    form = PromoCodeAdminForm(data=_add_payload(allowed_emails='not-an-email'))
    assert not form.is_valid()
    assert 'allowed_emails' in form.errors


def test_code_and_credits_freeze_once_claimed(client, admin_user):
    code = PromoCode.objects.create(code='LAUNCH2026', credits=100)
    client.force_login(admin_user)
    html = client.get(_change(code)).content.decode()
    assert 'name="code"' in html
    assert 'name="credits"' in html

    promo.redeem(_confirmed(), 'LAUNCH2026')

    html = client.get(_change(code)).content.decode()
    assert 'name="code"' not in html
    assert 'name="credits"' not in html
    assert 'name="ends_at_0"' in html  # still editable
    # The inline lists the redemption.
    assert 'granted' in html.lower()


def test_delete_only_while_never_redeemed(client, admin_user):
    code = PromoCode.objects.create(code='LAUNCH2026', credits=100)
    client.force_login(admin_user)
    delete_url = reverse('custom_admin:billing_promocode_delete', args=[code.pk])
    assert client.get(delete_url).status_code == 200

    promo.redeem(_confirmed(), 'LAUNCH2026')

    assert client.get(delete_url).status_code == 403
    assert PromoCode.objects.filter(pk=code.pk).exists()


def test_list_shows_usage_and_filters(client, admin_user):
    now = timezone.now()
    live = PromoCode.objects.create(code='LIVE1', credits=10, max_redemptions=3)
    PromoCode.objects.create(code='SOON1', credits=10, starts_at=now + timedelta(days=1))
    PromoCode.objects.create(code='GONE1', credits=10, ends_at=now - timedelta(days=1))
    full = PromoCode.objects.create(code='FULL1', credits=10, max_redemptions=1)
    PromoCode.objects.create(code='OFF11', credits=10, active=False)
    promo.redeem(_confirmed(), 'LIVE1')
    promo.reserve_for_signup(_user(), 'LIVE1')
    promo.redeem(_confirmed(), 'FULL1')
    client.force_login(admin_user)

    html = client.get(CHANGELIST).content.decode()
    assert '1 (+1 pending) / 3' in html

    def codes(state):
        page = client.get(CHANGELIST, {'state': state}).content.decode()
        return {c for c in ['LIVE1', 'SOON1', 'GONE1', 'FULL1', 'OFF11'] if f'>{c}<' in page}

    assert codes('live') == {'LIVE1'}
    assert codes('scheduled') == {'SOON1'}
    assert codes('expired') == {'GONE1'}
    assert codes('exhausted') == {'FULL1'}
    assert codes('inactive') == {'OFF11'}
    assert live.pk and full.pk


def test_duplicate_action(client, admin_user):
    source = PromoCode.objects.create(
        code='LAUNCH2026',
        credits=100,
        campaign='Launch',
        max_redemptions=5,
        allowed_emails=['a@x.com'],
    )
    client.force_login(admin_user)

    response = client.post(
        CHANGELIST,
        {'action': 'duplicate_action', '_selected_action': [str(source.pk)]},
        follow=True,
    )

    assert response.status_code == 200
    copies = PromoCode.objects.exclude(pk=source.pk)
    assert copies.count() == 1
    copy = copies.get()
    assert copy.code != source.code
    assert (copy.credits, copy.campaign, copy.max_redemptions, copy.allowed_emails) == (
        100,
        'Launch',
        5,
        ['a@x.com'],
    )
    assert copy.claimed_count == 0
    assert copy.created_by == admin_user


def test_export_action_and_campaign_export(client, admin_user):
    a = PromoCode.objects.create(code='AAAA1', credits=1, campaign='Fair')
    PromoCode.objects.create(code='BBBB1', credits=2, campaign='Other')
    client.force_login(admin_user)

    response = client.post(
        CHANGELIST, {'action': 'export_csv_action', '_selected_action': [str(a.pk)]}
    )
    assert response['Content-Type'].startswith('text/csv')
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    assert rows[0][0] == 'code'
    assert [r[0] for r in rows[1:]] == ['AAAA1']

    response = client.get(EXPORT, {'campaign': 'Fair'})
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    assert [r[0] for r in rows[1:]] == ['AAAA1']
    assert 'promo_codes_Fair.csv' in response['Content-Disposition']

    response = client.get(EXPORT)
    assert response.status_code == 302  # no campaign: back to the list with a message


def test_bulk_create(client, admin_user):
    client.force_login(admin_user)
    assert client.get(BULK).status_code == 200

    response = client.post(
        BULK,
        {
            'count': 5,
            'prefix': 'fair',
            'credits': 20,
            'campaign': 'Fair 2026',
            'starts_at': '',
            'ends_at': '',
            'new_accounts_within_days': '',
            'notes': 'printed cards',
        },
    )

    assert response.status_code == 302
    assert response['Location'] == f'{CHANGELIST}?campaign=Fair+2026'
    codes = PromoCode.objects.filter(campaign='Fair 2026')
    assert codes.count() == 5
    assert all(c.code.startswith('FAIR-') and c.max_redemptions == 1 for c in codes)
    assert all(
        c.credits == 20 and c.notes == 'printed cards' and c.created_by == admin_user for c in codes
    )
    assert len({c.code for c in codes}) == 5
    page = client.get(response['Location']).content.decode()
    assert 'Download them as CSV' in page
    assert 'Export campaign CSV' in page


def test_bulk_form_bounds():
    base = {'credits': 1, 'campaign': 'X', 'prefix': ''}
    assert not BulkCreateForm(data={**base, 'count': 1001}).is_valid()
    assert not BulkCreateForm(data={**base, 'count': 0}).is_valid()
    assert not BulkCreateForm(data={**base, 'count': 1, 'campaign': ''}).is_valid()
    assert not BulkCreateForm(data={**base, 'count': 1, 'prefix': 'no spaces'}).is_valid()
    assert BulkCreateForm(data={**base, 'count': 1}).is_valid()


def test_bulk_and_duplicate_need_the_add_permission(client):
    staff = _user(is_staff=True)
    staff.user_permissions.add(Permission.objects.get(codename='view_promocode'))  # ty: ignore[unresolved-attribute]
    client.force_login(staff)
    assert client.get(CHANGELIST).status_code == 200
    assert 'Create many' not in client.get(CHANGELIST).content.decode()

    response = client.post(BULK, {'count': 1, 'credits': 1, 'campaign': 'X'}, follow=True)
    assert PromoCode.objects.count() == 0
    assert 'You cannot add promo codes.' in response.content.decode()


def test_redemption_admin_is_read_only(client, admin_user):
    PromoCode.objects.create(code='LAUNCH2026', credits=100)
    redemption = promo.redeem(_confirmed(), 'LAUNCH2026')
    client.force_login(admin_user)

    html = client.get(reverse('custom_admin:billing_promoredemption_changelist')).content.decode()
    assert 'LAUNCH2026' in html
    assert redemption.user.email in html  # ty: ignore[unresolved-attribute]

    assert client.get(reverse('custom_admin:billing_promoredemption_add')).status_code == 403
    response = client.get(
        reverse('custom_admin:billing_promoredemption_change', args=[redemption.pk])
    )
    assert response.status_code == 200  # the read-only view
    assert 'name="status"' not in response.content.decode()
    assert PromoRedemption.objects.get().status == PromoRedemptionStatus.GRANTED


def test_deleting_a_user_with_redemptions_works(client, admin_user):
    PromoCode.objects.create(code='LAUNCH2026', credits=100)
    user = _confirmed()
    promo.redeem(user, 'LAUNCH2026')
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:accounts_user_delete', args=[user.pk]), {'post': 'yes'}
    )

    assert response.status_code == 302
    assert not PromoRedemption.objects.filter(user_id=user.pk).exists()
    assert PromoCode.objects.get().claimed_count == 1  # the use stays spent
