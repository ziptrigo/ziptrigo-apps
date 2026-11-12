"""Share links: creating, editing and revoking (rules, expiry in the owner's zone, the cap, the
read-only states), and when a link works."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.services.timezones import local_datetime

from ..models import ShareLink
from ..services import create_note, links, takedown, trash
from .factories import make_link, make_note, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def note(user, config):
    return create_note(user, title='Plan', content='# Plan\n\n- [ ] one\n')


def message(exc: pytest.ExceptionInfo[ValidationError]) -> str:
    return ' '.join(exc.value.messages)


class TestCreate:
    def test_a_link_has_an_unguessable_code_and_defaults_to_read_only(self, note):
        link = links.create_link(note, mode='read')
        assert len(link.code) == 22 and set(link.code) <= set(
            'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
        )
        assert (link.mode, link.label, link.expires_at, link.revoked_at) == ('read', '', None, None)
        assert link.last_edited_at is None
        other = links.create_link(note, mode='edit')
        assert other.code != link.code and other.mode == 'edit'

    def test_the_address_is_the_base_url_and_the_code(self, note, settings):
        settings.BASE_URL = 'https://app.example.com/'
        link = links.create_link(note, mode='read')
        assert links.link_url(link) == f'https://app.example.com/n/{link.code}'

    def test_the_label_is_cleaned_and_limited(self, note):
        link = links.create_link(note, mode='read', label='  For   Ann  ')
        assert link.label == 'For Ann'
        with pytest.raises(ValidationError) as exc:
            links.create_link(note, mode='read', label='x' * 101)
        assert message(exc) == 'Keep the label to 100 characters or fewer.'

    @pytest.mark.parametrize('mode', ['', 'admin', None])
    def test_the_mode_must_be_one_of_the_two(self, note, mode):
        with pytest.raises(ValidationError) as exc:
            links.create_link(note, mode=mode)
        assert 'mode' in exc.value.message_dict

    def test_the_cap_counts_active_links_only(self, note, config):
        config.max_links_per_note = 2
        config.save()
        first = links.create_link(note, mode='read')
        second = links.create_link(note, mode='read')
        with pytest.raises(ValidationError) as exc:
            links.create_link(note, mode='read')
        assert message(exc) == 'A note can have at most 2 active links.'
        links.revoke_link(first)
        make_link(note, expires_at=timezone.now() - timedelta(days=1))  # expired: not counted
        links.create_link(note, mode='edit')
        assert ShareLink.objects.filter(note=note).count() == 4
        assert second.pk

    def test_the_default_cap_is_ten(self, note):
        for _ in range(10):
            links.create_link(note, mode='read')
        with pytest.raises(ValidationError) as exc:
            links.create_link(note, mode='read')
        assert message(exc) == 'A note can have at most 10 active links.'

    def test_the_cap_is_per_note(self, user, note, config):
        config.max_links_per_note = 1
        config.save()
        links.create_link(note, mode='read')
        links.create_link(create_note(user, title='Other'), mode='read')

    def test_a_check_makes_the_same_refusals_without_writing(self, note, config):
        config.max_links_per_note = 1
        config.save()
        links.check_create(note, mode='read')
        links.create_link(note, mode='read')
        with pytest.raises(ValidationError):
            links.check_create(note, mode='read')
        with pytest.raises(ValidationError):
            links.check_create(note, mode='nope')
        assert ShareLink.objects.filter(note=note).count() == 1


class TestExpiry:
    def test_the_expiry_is_the_end_of_that_day_in_the_owners_zone(self, user, note, freeze_now):
        freeze_now(2026, 11, 10, 12, 0)
        user.timezone = 'Asia/Tokyo'
        user.save()
        note.user = user
        link = links.create_link(note, mode='read', expires_on=date(2026, 11, 12))
        assert link.expires_at == datetime(2026, 11, 12, 14, 59, 59, tzinfo=UTC)  # 23:59:59 JST
        assert link.expires_at == local_datetime(user, date(2026, 11, 12), time(23, 59, 59))

    def test_a_date_that_has_passed_is_refused(self, note, freeze_now):
        freeze_now(2026, 11, 10, 12, 0)
        with pytest.raises(ValidationError) as exc:
            links.create_link(note, mode='read', expires_on=date(2026, 11, 9))
        assert exc.value.message_dict == {'expires_on': ['Choose a date that has not passed.']}

    def test_today_is_allowed_until_its_last_second(self, note, freeze_now):
        freeze_now(2026, 11, 10, 23, 0)
        link = links.create_link(note, mode='read', expires_on=date(2026, 11, 10))
        assert link.is_active()

    def test_a_link_expires_when_its_day_ends(self, note):
        link = links.create_link(
            note, mode='read', expires_on=timezone.now().date() + timedelta(days=2)
        )
        assert link.is_active() and links.state(link) == 'active'
        assert link.expires_at is not None
        assert links.state(link, link.expires_at + timedelta(seconds=1)) == 'expired'
        assert not link.is_expired(link.expires_at - timedelta(seconds=1))


class TestUpdate:
    def test_mode_label_and_expiry_change(self, note):
        link = links.create_link(note, mode='read', label='A')
        link.note = note
        tomorrow = timezone.now().date() + timedelta(days=3)
        updated = links.update_link(link, mode='edit', label='B', expires_on=tomorrow)
        assert (updated.mode, updated.label) == ('edit', 'B')
        assert updated.expires_at is not None and updated.expires_at.date() >= tomorrow
        cleared = links.update_link(updated, clear_expiry=True)
        assert cleared.expires_at is None
        assert links.update_link(cleared, label='').label == ''

    def test_a_field_left_out_is_unchanged(self, note):
        link = links.create_link(note, mode='edit', label='A', expires_on=date(2099, 1, 1))
        link.note = note
        same = links.update_link(link)
        assert (same.mode, same.label, same.expires_at) == (link.mode, link.label, link.expires_at)

    def test_the_date_it_already_expires_on_is_no_change_so_an_expired_links_label_can_be_edited(
        self, note, freeze_now
    ):
        freeze_now(2026, 11, 10, 12, 0)
        link = links.create_link(note, mode='read', expires_on=date(2026, 11, 11))
        link.note = note
        freeze_now(2026, 11, 20, 12, 0)
        assert link.is_expired()
        updated = links.update_link(link, label='Old', expires_on=date(2026, 11, 11))
        assert updated.label == 'Old' and updated.expires_at == link.expires_at

    def test_a_new_date_in_the_past_is_refused(self, note):
        link = links.create_link(note, mode='read')
        link.note = note
        with pytest.raises(ValidationError) as exc:
            links.update_link(link, expires_on=date(2020, 1, 1))
        assert 'expires_on' in exc.value.message_dict

    def test_extending_an_expired_link_makes_it_active_again_and_counts_against_the_cap(
        self, note, config
    ):
        config.max_links_per_note = 1
        config.save()
        expired = make_link(note, expires_at=timezone.now() - timedelta(days=1))
        expired.note = note
        links.create_link(note, mode='read')  # the one active link
        with pytest.raises(ValidationError) as exc:
            links.update_link(expired, expires_on=timezone.now().date() + timedelta(days=5))
        assert message(exc) == 'A note can have at most 1 active links.'

    def test_a_revoked_link_cannot_be_changed(self, note):
        link = links.create_link(note, mode='read')
        link.note = note
        links.revoke_link(link)
        with pytest.raises(ValidationError) as exc:
            links.update_link(link, label='x')
        assert message(exc) == 'This link was revoked and can no longer be changed.'

    def test_a_check_makes_the_same_refusals(self, note):
        link = links.create_link(note, mode='read')
        link.note = note
        links.check_update(link, mode='edit', label='ok')
        with pytest.raises(ValidationError):
            links.check_update(link, mode='bogus')
        with pytest.raises(ValidationError):
            links.check_update(link, expires_on=date(2020, 1, 1))


class TestRevoke:
    def test_revoking_is_idempotent_and_final(self, note):
        link = links.create_link(note, mode='read')
        link.note = note
        first = links.revoke_link(link)
        stamp = first.revoked_at
        assert stamp is not None
        again = links.revoke_link(link)
        assert again.revoked_at == stamp
        assert links.state(again) == 'revoked' and not again.is_active()
        assert not links.is_available(again)

    def test_the_code_is_never_reissued(self, note):
        link = links.create_link(note, mode='read')
        link.note = note
        links.revoke_link(link)
        assert ShareLink.objects.filter(code=link.code).count() == 1


class TestReadOnlyNotes:
    @pytest.mark.parametrize(
        ('state', 'text'),
        [
            ('trashed', 'This note is in the Trash.'),
            ('locked', 'This note is locked until its renewal is paid.'),
            ('taken_down', 'This note was taken down and cannot be shared.'),
        ],
    )
    def test_creating_and_editing_are_refused(self, user, config, state, text):
        note = create_note(user, title='A')
        link = links.create_link(note, mode='read')
        link.note = note
        if state == 'trashed':
            trash(note)
        elif state == 'locked':
            type(note).objects.filter(pk=note.pk).update(locked_at=timezone.now())
        else:
            takedown.take_down_note(note, by=make_user(is_staff=True), reason='x')
        fresh = type(note).objects.select_related('user').get(pk=note.pk)
        link.note = fresh
        for call in (
            lambda: links.create_link(fresh, mode='read'),
            lambda: links.update_link(link, label='x'),
        ):
            with pytest.raises(ValidationError) as exc:
                call()
            assert message(exc) == text

    @pytest.mark.parametrize('state', ['trashed', 'locked', 'taken_down'])
    def test_revoking_is_always_allowed(self, user, config, state):
        """Revoking only reduces what is exposed: an owner who learns a link leaked can kill it
        while the note is locked, in the Trash or taken down."""
        note = create_note(user, title='A')
        link = links.create_link(note, mode='edit')
        link.note = note
        if state == 'trashed':
            trash(note)
        elif state == 'locked':
            type(note).objects.filter(pk=note.pk).update(locked_at=timezone.now())
        else:
            takedown.take_down_note(note, by=make_user(is_staff=True), reason='x')
        fresh = type(note).objects.select_related('user').get(pk=note.pk)
        link.note = fresh
        assert links.revoke_link(link).revoked_at is not None
        assert ShareLink.objects.get(pk=link.pk).revoked_at is not None
        assert links.revoke_link(link).revoked_at is not None  # idempotent

    def test_a_takedown_is_reported_before_the_lock(self, user, config):
        from ..services import require_writable

        note = create_note(user, title='A')
        takedown.take_down_note(note, by=make_user(is_staff=True), reason='x')
        fresh = type(note).objects.select_related('user').get(pk=note.pk)
        fresh.locked_at = timezone.now()
        with pytest.raises(ValidationError) as exc:
            require_writable(fresh)
        assert 'taken down' in message(exc)
        with pytest.raises(ValidationError) as exc:
            links.require_shareable(fresh)
        assert 'taken down' in message(exc)

    def test_check_update_applies_the_cap_to_an_expired_link_made_active_again(self, user, config):
        note = create_note(user, title='A')
        config.max_links_per_note = 1
        config.save()
        link = links.create_link(note, mode='read')
        link.note = note
        ShareLink.objects.filter(pk=link.pk).update(expires_at=timezone.now() - timedelta(1))
        link.refresh_from_db()
        link.note = note
        links.create_link(note, mode='read')  # takes the one active slot
        later = timezone.localdate() + timedelta(days=5)
        with pytest.raises(ValidationError):
            links.check_update(link, expires_on=later)
        with pytest.raises(ValidationError):
            links.check_update(link, clear_expiry=True)
        links.check_update(link, label='still editable')  # no reactivation: no cap involved


class TestAvailability:
    def test_an_active_link_of_a_normal_note_works(self, note):
        link = links.create_link(note, mode='read')
        found = links.get_available(link.code)
        assert found is not None and found.pk == link.pk and found.note.pk == note.pk

    def test_codes_are_case_sensitive(self, note):
        link = links.create_link(note, mode='read')
        assert links.get_available(link.code.swapcase()) is None
        assert links.get_available('') is None
        assert links.get_available('x' * 65) is None

    @pytest.mark.parametrize('state', ['revoked', 'expired', 'trashed', 'locked', 'taken_down'])
    def test_every_reason_it_stops_working(self, user, config, state):
        note = create_note(user, title='A')
        link = links.create_link(note, mode='read')
        link.note = note
        if state == 'revoked':
            links.revoke_link(link)
        elif state == 'expired':
            ShareLink.objects.filter(pk=link.pk).update(expires_at=timezone.now() - timedelta(1))
        elif state == 'trashed':
            trash(note)
        elif state == 'locked':
            type(note).objects.filter(pk=note.pk).update(locked_at=timezone.now())
        else:
            takedown.take_down_note(note, by=make_user(is_staff=True), reason='x')
        assert links.get_available(link.code) is None

    def test_archiving_changes_nothing_for_links(self, user, note):
        from ..services import archive

        link = links.create_link(note, mode='read')
        archive(note)
        assert links.get_available(link.code) is not None

    def test_a_restored_note_works_again(self, user, note):
        from ..services import restore

        link = links.create_link(note, mode='read')
        trash(note)
        assert links.get_available(link.code) is None
        restore(note)
        assert links.get_available(link.code) is not None


class TestReading:
    def test_the_list_has_active_links_first_with_their_state_and_address(self, note):
        old = links.create_link(note, mode='read', label='old')
        old.note = note
        links.revoke_link(old)
        newest = links.create_link(note, mode='edit', label='new')
        items = links.list_links(note)
        assert [item.pk for item in items] == [newest.pk, old.pk]
        assert [item.state for item in items] == ['active', 'revoked']
        assert items[0].url.endswith(f'/n/{newest.code}')
        assert items[0].expiry_value == ''

    def test_the_flag_annotates_a_list_in_one_query(self, user, config, django_assert_num_queries):
        shared = create_note(user, title='Shared')
        plain = create_note(user, title='Plain')
        links.create_link(shared, mode='read')
        revoked = links.create_link(plain, mode='read')
        revoked.note = plain
        links.revoke_link(revoked)
        with django_assert_num_queries(1):
            flags = {
                n.title: n.has_active_link
                for n in links.with_active_link_flag(type(shared).objects.filter(user=user))
            }
        assert flags == {'Shared': True, 'Plain': False}

    def test_a_qr_code_is_a_png_data_uri_of_the_address(self, note):
        uri = links.qr_data_uri('https://example.com/n/abc')
        assert uri.startswith('data:image/png;base64,')
        assert links.qr_data_uri('https://example.com/n/abc') is uri  # cached per address


def test_another_users_link_is_not_found_by_code_confusion(user, other_user, config):
    mine = create_note(user, title='A')
    theirs = make_note(other_user, title='B')
    links.create_link(mine, mode='read')
    assert links.active_links(theirs).count() == 0
