"""Share links: creating, editing and revoking (rules, expiry in the owner's zone, the cap, a
deleted code), and when a link works."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.services.timezones import local_datetime
from apps.qr_code.models import QRCode, QRShareLink
from apps.qr_code.services import share

from .share_support import make_link, make_qrcode

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture
def qrcode(user):
    return make_qrcode(user)


def message(exc: pytest.ExceptionInfo[ValidationError]) -> str:
    return ' '.join(exc.value.messages)


def tomorrow() -> date:
    return (timezone.now() + timedelta(days=2)).date()


class TestCreate:
    def test_a_link_has_an_unguessable_code(self, qrcode):
        link = share.create_link(qrcode)
        assert len(link.code) == 22 and set(link.code) <= set(
            'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
        )
        assert (link.label, link.expires_at, link.revoked_at) == ('', None, None)
        assert share.create_link(qrcode).code != link.code

    def test_the_address_is_the_base_url_and_the_code(self, qrcode, settings):
        settings.BASE_URL = 'https://app.example.com/'
        link = share.create_link(qrcode)
        assert share.link_url(link) == f'https://app.example.com/q/{link.code}'
        assert share.link_path(link) == f'/q/{link.code}'

    def test_the_label_is_cleaned_and_limited(self, qrcode):
        assert share.create_link(qrcode, label='  For   the  printer ').label == 'For the printer'
        with pytest.raises(ValidationError) as exc:
            share.create_link(qrcode, label='x' * 101)
        assert message(exc) == 'Keep the label to 100 characters or fewer.'
        assert 'label' in exc.value.message_dict

    def test_the_cap_counts_active_links_only(self, qrcode):
        links = [share.create_link(qrcode) for _ in range(10)]
        with pytest.raises(ValidationError) as exc:
            share.create_link(qrcode)
        assert message(exc) == 'A QR code can have at most 10 active links.'
        share.revoke_link(links[0])
        make_link(qrcode, expires_at=timezone.now() - timedelta(days=1))  # expired: not counted
        share.create_link(qrcode)
        assert QRShareLink.objects.filter(qrcode=qrcode).count() == 12

    def test_the_cap_is_per_qr_code(self, user, qrcode):
        for _ in range(10):
            share.create_link(qrcode)
        share.create_link(make_qrcode(user, name='Other'))

    def test_a_deleted_qr_code_cannot_be_shared(self, qrcode):
        qrcode.soft_delete()
        with pytest.raises(ValidationError) as exc:
            share.create_link(qrcode)
        assert message(exc) == 'This QR code was deleted.'
        with pytest.raises(ValidationError):
            share.check_create(qrcode)

    def test_a_deleted_code_is_refused_even_when_the_instance_in_hand_is_stale(self, qrcode):
        stale = QRCode.objects.select_related('created_by').get(pk=qrcode.pk)
        qrcode.soft_delete()
        assert stale.deleted_at is None
        with pytest.raises(ValidationError):  # decided again under the lock
            share.create_link(stale)

    def test_a_cap_refusal_leaves_no_link_behind(self, qrcode):
        for _ in range(10):
            share.create_link(qrcode)
        with pytest.raises(ValidationError):
            share.create_link(qrcode)
        assert QRShareLink.objects.filter(qrcode=qrcode).count() == 10


class TestExpiry:
    def test_a_date_means_the_end_of_that_day_in_the_owners_zone(self, user, qrcode):
        user.timezone = 'Asia/Tokyo'
        user.save()
        qrcode.created_by = user
        day = tomorrow()
        link = share.create_link(qrcode, expires_on=day)
        assert link.expires_at == datetime.combine(day, time(23, 59, 59), tzinfo=UTC) - timedelta(
            hours=9
        )
        assert share.expiry_date(link) == day

    def test_with_no_zone_it_is_utc(self, qrcode):
        day = tomorrow()
        link = share.create_link(qrcode, expires_on=day)
        assert link.expires_at == datetime.combine(day, time(23, 59, 59), tzinfo=UTC)

    def test_a_past_date_is_refused(self, qrcode):
        for bad in (timezone.now().date() - timedelta(days=1), date(2000, 1, 1)):
            with pytest.raises(ValidationError) as exc:
                share.create_link(qrcode, expires_on=bad)
            assert message(exc) == 'Choose a date that has not passed.'
            assert 'expires_on' in exc.value.message_dict

    def test_today_is_refused_when_its_end_has_passed_in_the_owners_zone(self, user, qrcode):
        # The end of "yesterday" in the far east is long gone, and of "today" in the far west is not.
        user.timezone = 'Pacific/Kiritimati'  # UTC+14
        user.save()
        qrcode.created_by = user
        local_today = timezone.now().astimezone(ZoneInfo(user.timezone)).date()
        share.create_link(qrcode, expires_on=local_today)  # its end is still ahead
        with pytest.raises(ValidationError):
            share.create_link(qrcode, expires_on=local_today - timedelta(days=1))

    def test_a_datetime_is_that_instant_and_a_naive_one_is_read_in_the_owners_zone(
        self, user, qrcode
    ):
        user.timezone = 'America/Chicago'
        user.save()
        qrcode.created_by = user
        aware = timezone.now() + timedelta(days=3)
        assert share.create_link(qrcode, expires_on=aware).expires_at == aware
        naive = datetime.combine(tomorrow(), time(10, 0))
        link = share.create_link(qrcode, expires_on=naive)
        assert link.expires_at == local_datetime(user, naive.date(), time(10, 0))

    def test_a_naive_datetime_in_a_skipped_hour_follows_local_datetimes_rule(self, user, qrcode):
        user.timezone = 'Europe/Lisbon'
        user.save()
        qrcode.created_by = user
        naive = datetime(2030, 3, 31, 1, 30)  # skipped when the clocks went forward that day
        link = share.create_link(qrcode, expires_on=naive)
        assert link.expires_at == local_datetime(user, naive.date(), time(1, 30))

    def test_a_past_datetime_is_refused(self, qrcode):
        with pytest.raises(ValidationError):
            share.create_link(qrcode, expires_on=timezone.now() - timedelta(minutes=1))


class TestLabels:
    def test_control_and_format_characters_are_dropped(self, qrcode):
        raw = 'for\u200b Ann\u202e \x07 and\n  Bob'
        assert share.create_link(qrcode, label=raw).label == 'for Ann and Bob'
        assert share.clean_label('\u200b\u202e') == ''


class TestUpdate:
    def test_the_label_and_the_expiry_change(self, qrcode):
        link = share.create_link(qrcode, label='old')
        link.qrcode = qrcode
        day = tomorrow()
        updated = share.update_link(link, label='new', expires_on=day)
        assert updated.label == 'new' and share.expiry_date(updated) == day
        cleared = share.update_link(updated, clear_expiry=True)
        assert cleared.expires_at is None
        assert share.update_link(cleared, label=None).label == 'new'  # None leaves it

    def test_none_as_the_expiry_also_clears_it(self, qrcode):
        link = share.create_link(qrcode, expires_on=tomorrow())
        link.qrcode = qrcode
        assert share.update_link(link, expires_on=None).expires_at is None

    def test_a_revoked_link_cannot_be_changed(self, qrcode):
        link = share.create_link(qrcode)
        link.qrcode = qrcode
        share.revoke_link(link)
        link.refresh_from_db()
        link.qrcode = qrcode
        for call in (share.update_link, share.check_update):
            with pytest.raises(ValidationError) as exc:
                call(link, label='x')
            assert message(exc) == 'This link was revoked and can no longer be changed.'

    def test_an_expired_links_label_can_be_edited_by_keeping_its_date(self, qrcode):
        link = make_link(qrcode, expires_at=timezone.now() - timedelta(days=1))
        link.qrcode = qrcode
        day = share.expiry_date(link)
        updated = share.update_link(link, label='late', expires_on=day)
        assert updated.label == 'late' and updated.expires_at == link.expires_at

    def test_extending_an_expired_link_counts_against_the_cap(self, qrcode):
        lapsed = make_link(qrcode, expires_at=timezone.now() - timedelta(days=1))
        lapsed.qrcode = qrcode
        for _ in range(10):
            share.create_link(qrcode)
        with pytest.raises(ValidationError) as exc:
            share.update_link(lapsed, expires_on=tomorrow())
        assert message(exc) == 'A QR code can have at most 10 active links.'
        with pytest.raises(ValidationError):
            share.check_update(lapsed, clear_expiry=True)
        lapsed.refresh_from_db()
        assert lapsed.is_expired()

    def test_a_past_date_is_refused(self, qrcode):
        link = share.create_link(qrcode)
        link.qrcode = qrcode
        with pytest.raises(ValidationError) as exc:
            share.update_link(link, expires_on=date(2000, 1, 1))
        assert 'expires_on' in exc.value.message_dict

    def test_a_deleted_qr_code_cannot_have_its_links_changed(self, qrcode):
        link = share.create_link(qrcode)
        link.qrcode = qrcode
        qrcode.soft_delete()
        with pytest.raises(ValidationError):
            share.update_link(link, label='x')


class TestRevoke:
    def test_revoking_is_idempotent_and_final(self, qrcode):
        link = share.create_link(qrcode)
        link.qrcode = qrcode
        first = share.revoke_link(link)
        assert first.revoked_at is not None
        again = share.revoke_link(link)
        assert again.revoked_at == first.revoked_at
        assert share.state(again) == 'revoked'

    def test_revoking_is_allowed_for_a_deleted_qr_code(self, qrcode):
        link = share.create_link(qrcode)
        link.qrcode = qrcode
        qrcode.soft_delete()
        assert share.revoke_link(link).revoked_at is not None


class TestAvailability:
    def test_an_active_link_works(self, qrcode):
        link = share.create_link(qrcode)
        found = share.get_available(link.code)
        assert found is not None and found.pk == link.pk and found.qrcode.pk == qrcode.pk

    def test_every_dead_link_is_none_alike(self, user, qrcode):
        revoked = share.create_link(qrcode)
        revoked.qrcode = qrcode
        share.revoke_link(revoked)
        expired = make_link(qrcode, expires_at=timezone.now() - timedelta(seconds=1))
        gone = make_qrcode(user, name='Gone')
        on_deleted = share.create_link(gone)
        gone.soft_delete()
        for code in (revoked.code, expired.code, on_deleted.code, 'nope', '', 'x' * 65):
            assert share.get_available(code) is None

    @pytest.mark.parametrize('change', [{'is_active': False}, {'status': 'INACTIVE'}])
    def test_a_link_of_an_account_that_cannot_sign_in_does_not_work(self, user, qrcode, change):
        link = share.create_link(qrcode)
        assert share.get_available(link.code) is not None
        for field, value in change.items():
            setattr(user, field, value)
        user.save()
        assert share.get_available(link.code) is None
        link = QRShareLink.objects.select_related('qrcode__created_by').get(pk=link.pk)
        assert not share.is_available(link)

    def test_a_code_is_case_sensitive(self, qrcode):
        link = make_link(qrcode, code='AbCdEf123456')
        assert share.get_available('AbCdEf123456') is not None
        assert share.get_available(link.code.lower()) is None
        assert share.get_available(link.code.upper()) is None

    def test_is_available_reflects_state(self, qrcode):
        link = share.create_link(qrcode)
        link.qrcode = qrcode
        assert share.is_available(link)
        qrcode.soft_delete()
        assert not share.is_available(link)

    def test_it_shows_the_qr_code_as_it_is_now(self, qrcode):
        link = share.create_link(qrcode)
        QRCode.objects.filter(pk=qrcode.pk).update(name='Renamed')
        found = share.get_available(link.code)
        assert found is not None and found.qrcode.name == 'Renamed'


class TestReading:
    def test_list_links_puts_active_first_and_fills_the_panel_fields(self, qrcode):
        old = share.create_link(qrcode, label='old')
        old.qrcode = qrcode
        share.revoke_link(old)
        lapsed = make_link(qrcode, expires_at=timezone.now() - timedelta(days=1))
        live = share.create_link(qrcode, label='live', expires_on=tomorrow())
        listed = share.list_links(qrcode)
        assert listed[0].pk == live.pk
        assert {link.pk: link.state for link in listed} == {
            live.pk: 'active',
            old.pk: 'revoked',
            lapsed.pk: 'expired',
        }
        assert (
            listed[0].url == share.link_url(live)
            and listed[0].expiry_value == tomorrow().isoformat()
        )
        assert listed[0].expires_field_id == f'expires-{live.pk}'

    def test_list_links_makes_two_queries_whatever_the_links_and_people(
        self, qrcode, django_assert_num_queries
    ):
        """The links, and the people each was emailed to in one prefetch."""
        from apps.qr_code.models import QRShareRecipient

        for i in range(4):
            link = share.create_link(qrcode, expires_on=tomorrow())
            for j in range(3):
                QRShareRecipient.objects.create(link=link, email=f'p{i}{j}@example.com')
        with django_assert_num_queries(2):
            listed = share.list_links(qrcode)
            assert sum(len(link.recipients.all()) for link in listed) == 12

    def test_the_active_flag_is_one_subquery(self, user, qrcode, django_assert_num_queries):
        other = make_qrcode(user, name='Other')
        revoked = share.create_link(other)
        revoked.qrcode = other
        share.revoke_link(revoked)
        share.create_link(qrcode)
        make_qrcode(user, name='Plain')
        with django_assert_num_queries(1):
            flags = {
                q.name: q.has_active_link
                for q in share.with_active_link_flag(QRCode.objects.filter(created_by=user))
            }
        assert flags == {'Shop door': True, 'Other': False, 'Plain': False}

    def test_check_create_writes_nothing(self, qrcode):
        share.check_create(qrcode, label='fine', expires_on=tomorrow())
        assert not QRShareLink.objects.exists()
