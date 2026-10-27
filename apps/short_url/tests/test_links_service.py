from dataclasses import replace
from datetime import timedelta
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.billing.models import CreditTransaction
from apps.billing.services import InsufficientCreditsError, add_credits

from ..models import LinkStatus, ShortLink
from ..services import (
    UNSET,
    AliasTakenError,
    CostChangedError,
    LimitReachedError,
    LinkInput,
    analytics_unlocked,
    create_link,
    delete_link,
    display_state,
    enable_analytics,
    enable_costs,
    is_available,
    normalize_tags,
    quote_analytics,
    quote_create,
    quote_enable,
    quote_update,
    set_enabled,
    short_url,
    update_link,
)
from ..services import links as links_module
from .conftest import balance
from .factories import create_link as make_link
from .factories import expired, make_user

pytestmark = pytest.mark.django_db


def new(user, destination='https://example.com/page', **fields) -> ShortLink:
    return create_link(user, LinkInput(destination=destination, **fields))


class TestCreate:
    def test_a_random_code_is_issued(self, funded_user):
        link = new(funded_user)
        assert len(link.code) == 7
        assert link.code_key == link.code.lower()
        assert not link.is_alias
        assert link.owner == funded_user
        assert link.status == LinkStatus.ACTIVE
        assert link.filter_bots is True
        assert link.click_count == 0
        assert ShortLink.objects.get(pk=link.pk).destination == 'https://example.com/page'

    def test_it_is_free_by_default(self, funded_user):
        new(funded_user)
        assert balance(funded_user) == 10
        assert not CreditTransaction.objects.filter(source='short_url').exists()

    def test_an_alias_is_the_code_and_costs_credits_once(self, funded_user):
        link = new(funded_user, alias='Spring-Sale')
        assert link.code == 'Spring-Sale'
        assert link.code_key == 'spring-sale'
        assert link.is_alias
        assert link.alias_paid_at is not None
        assert balance(funded_user) == 9

    def test_an_alias_cannot_be_taken_in_another_case(self, funded_user, other_user):
        add_credits(other_user, 5, description='x', source='test')
        new(funded_user, alias='Sale')
        with pytest.raises(AliasTakenError):
            new(other_user, alias='SALE')
        assert balance(other_user) == 5

    def test_a_reserved_alias_is_refused(self, funded_user):
        with pytest.raises(AliasTakenError):
            new(funded_user, alias='admin')
        assert balance(funded_user) == 10

    def test_losing_the_race_for_an_alias_is_a_clean_refusal(self, funded_user, other_user):
        """The alias was free when it was checked and gone when it was saved."""
        make_link(other_user, code='racy')
        with mock.patch.object(links_module.codes, 'validate_alias', lambda alias: alias):
            with pytest.raises(AliasTakenError):
                new(funded_user, alias='racy')
        assert ShortLink.objects.filter(owner=funded_user).count() == 0
        assert balance(funded_user) == 10

    def test_fields_are_validated_and_stored(self, funded_user):
        expires = timezone.now() + timedelta(days=3)
        link = new(
            funded_user,
            title='  My   page ',
            tags='News, SALE,  big deal ,news',
            expires_at=expires,
            max_clicks=50,
        )
        link.refresh_from_db()
        assert link.title == 'My page'
        assert link.tags == ['news', 'sale', 'big-deal']
        assert link.expires_at == expires
        assert link.max_clicks == 50

    def test_utm_is_merged_into_the_destination(self, funded_user):
        link = new(
            funded_user,
            destination='https://example.com/p?a=1',
            utm={'utm_source': 'news', 'utm_medium': 'email'},
        )
        assert link.destination == 'https://example.com/p?a=1&utm_source=news&utm_medium=email'

    @pytest.mark.parametrize(
        ('fields', 'field'),
        [
            ({'destination': 'ftp://example.com'}, 'destination'),
            ({'destination': 'http://127.0.0.1/'}, 'destination'),
            ({'destination': 'https://app.example.test/s/abc'}, 'destination'),
            ({'title': 'x' * 256}, 'title'),
            ({'tags': 'bad tag!'}, 'tags'),
            ({'tags': ','.join(f't{i}' for i in range(21))}, 'tags'),
            ({'tags': 'a' * 33}, 'tags'),
            ({'max_clicks': 0}, 'max_clicks'),
            ({'max_clicks': 2_000_000_001}, 'max_clicks'),
            ({'expires_at': timezone.now() - timedelta(minutes=1)}, 'expires_at'),
            ({'alias': 'no'}, 'alias'),
            ({'alias': 'has space'}, 'alias'),
        ],
    )
    def test_bad_fields_are_refused_and_nothing_is_saved(self, funded_user, fields, field):
        data = replace(LinkInput(destination='https://example.com/page'), **fields)
        with pytest.raises(ValidationError) as caught:
            create_link(funded_user, data)
        assert field in caught.value.message_dict
        assert not ShortLink.objects.exists()
        assert balance(funded_user) == 10

    def test_twenty_tags_are_fine(self, funded_user):
        link = new(funded_user, tags=[f't{i}' for i in range(20)])
        assert len(link.tags) == 20

    def test_a_naive_expiry_is_read_in_the_site_time_zone(self, funded_user):
        naive = (timezone.now() + timedelta(days=1)).replace(tzinfo=None)
        link = new(funded_user, expires_at=naive)
        assert timezone.is_aware(link.expires_at)

    def test_a_title_is_fetched_in_the_background_only_when_there_is_none(
        self, funded_user, django_capture_on_commit_callbacks
    ):
        with mock.patch.object(links_module, 'fetch_title') as task:
            with django_capture_on_commit_callbacks(execute=True):
                untitled = new(funded_user)
                new(funded_user, destination='https://example.com/other', title='Mine')
        task.enqueue.assert_called_once_with(str(untitled.pk))

    def test_nothing_is_queued_when_it_rolls_back(
        self, user, prices, django_capture_on_commit_callbacks
    ):
        with mock.patch.object(links_module, 'fetch_title') as task:
            with django_capture_on_commit_callbacks(execute=True):
                with pytest.raises(InsufficientCreditsError):
                    new(user, alias='broke')
        task.enqueue.assert_not_called()
        assert not ShortLink.objects.exists()

    def test_not_enough_credits_for_an_alias_creates_nothing(self, user, prices):
        with pytest.raises(InsufficientCreditsError):
            new(user, alias='broke')
        assert not ShortLink.objects.exists()


class TestExtraLinks:
    """`prices` allows 2 free active links and 5 in all; an extra one costs 1 credit."""

    def test_links_within_the_allowance_are_free(self, funded_user):
        new(funded_user)
        new(funded_user)
        assert balance(funded_user) == 10

    def test_the_next_one_costs_the_extra_link_price_once(self, funded_user):
        new(funded_user)
        new(funded_user)
        third = new(funded_user)
        assert third.extra_link_paid_at is not None
        assert balance(funded_user) == 9
        fourth = new(funded_user)
        assert fourth.extra_link_paid_at is not None
        assert balance(funded_user) == 8

    def test_an_alias_and_an_extra_link_are_both_charged(self, funded_user):
        new(funded_user)
        new(funded_user)
        new(funded_user, alias='both')
        assert balance(funded_user) == 8

    def test_the_hard_cap_refuses_creation(self, funded_user):
        for _ in range(5):
            new(funded_user)
        with pytest.raises(LimitReachedError):
            new(funded_user)
        assert ShortLink.objects.filter(owner=funded_user).count() == 5

    def test_disabled_deleted_and_taken_down_links_do_not_use_the_free_allowance(self, funded_user):
        make_link(funded_user, status=LinkStatus.DISABLED)
        make_link(funded_user, status=LinkStatus.DELETED)
        make_link(funded_user, status=LinkStatus.TAKEN_DOWN)
        new(funded_user)
        new(funded_user)
        assert balance(funded_user) == 10

    def test_disabled_links_count_toward_the_hard_cap(self, funded_user):
        for _ in range(3):
            make_link(funded_user)
        for _ in range(2):
            make_link(funded_user, status=LinkStatus.DISABLED)
        with pytest.raises(LimitReachedError):
            new(funded_user)

    def test_deleted_links_make_room_under_the_hard_cap(self, funded_user):
        for _ in range(5):
            make_link(funded_user, status=LinkStatus.DELETED)
        new(funded_user)

    def test_another_users_links_do_not_count(self, funded_user, other_user):
        for _ in range(4):
            make_link(other_user)
        new(funded_user)
        assert balance(funded_user) == 10

    def test_running_out_of_credits_on_the_extra_link_creates_nothing(self, user, prices):
        new(user)
        new(user)
        with pytest.raises(InsufficientCreditsError):
            new(user)
        assert ShortLink.objects.filter(owner=user).count() == 2

    def test_zero_priced_extra_links_are_free(self, funded_user, prices):
        prices.extra_link_price = 0
        prices.save()
        for _ in range(4):
            new(funded_user)
        assert balance(funded_user) == 10

    def test_quote_create(self, funded_user):
        assert not quote_create(funded_user, None)
        assert quote_create(funded_user, 'alias').total == 1
        assert not quote_create(funded_user, '   ')
        new(funded_user)
        new(funded_user)
        assert quote_create(funded_user, None).total == 1
        assert quote_create(funded_user, 'alias').total == 2


class TestUpdate:
    def test_changes_only_what_was_given(self, funded_user):
        link = new(funded_user, title='Keep', tags='a')
        update_link(link, destination='https://example.com/new')
        link.refresh_from_db()
        assert link.destination == 'https://example.com/new'
        assert link.title == 'Keep'
        assert link.tags == ['a']

    def test_the_code_never_changes(self, funded_user):
        link = new(funded_user, alias='stay')
        update_link(link, destination='https://example.com/new', title='x')
        link.refresh_from_db()
        assert (link.code, link.code_key, link.is_alias) == ('stay', 'stay', True)

    def test_all_fields(self, funded_user):
        link = new(funded_user)
        expires = timezone.now() + timedelta(days=2)
        update_link(
            link,
            destination='https://example.com/x',
            utm={'utm_campaign': 'c'},
            title='T',
            tags='x, y',
            expires_at=expires,
            max_clicks=9,
        )
        link.refresh_from_db()
        assert link.destination == 'https://example.com/x?utm_campaign=c'
        assert (link.title, link.tags, link.expires_at, link.max_clicks) == (
            'T',
            ['x', 'y'],
            expires,
            9,
        )

    def test_none_clears_an_expiry_and_a_click_cap(self, funded_user):
        link = new(funded_user, expires_at=timezone.now() + timedelta(days=1), max_clicks=3)
        update_link(link, expires_at=None, max_clicks=None)
        link.refresh_from_db()
        assert link.expires_at is None
        assert link.max_clicks is None

    def test_a_bad_destination_changes_nothing_at_all(self, funded_user):
        link = new(funded_user, title='Old')
        with pytest.raises(ValidationError):
            update_link(link, title='New', destination='http://10.0.0.1/')
        link.refresh_from_db()
        assert link.title == 'Old'

    def test_a_later_bad_field_rolls_back_the_earlier_ones(self, funded_user):
        link = new(funded_user, title='Old')
        with pytest.raises(ValidationError):
            update_link(link, destination='https://example.com/new', max_clicks=0)
        link.refresh_from_db()
        assert link.destination == 'https://example.com/page'

    def test_the_destination_is_validated_on_every_edit(self, funded_user):
        link = new(funded_user)
        for bad in ('javascript:alert(1)', 'https://app.example.test/go/x', 'http://localhost/'):
            with pytest.raises(ValidationError):
                update_link(link, destination=bad)

    def test_utm_alone_is_merged_into_the_current_destination(self, funded_user):
        link = new(funded_user, destination='https://example.com/p?x=1')
        update_link(link, utm={'utm_source': 's'})
        link.refresh_from_db()
        assert link.destination == 'https://example.com/p?x=1&utm_source=s'

    def test_an_expiry_in_the_past_is_refused_unless_unchanged(self, funded_user):
        link = expired(funded_user)
        with pytest.raises(ValidationError):
            update_link(link, expires_at=timezone.now() - timedelta(days=1))
        # Saving the form with the expiry it already has is not a change.
        update_link(link, expires_at=link.expires_at, title='Fine')
        link.refresh_from_db()
        assert link.title == 'Fine'

    def test_no_change_writes_nothing(self, funded_user):
        link = new(funded_user)
        before = ShortLink.objects.get(pk=link.pk).updated_at
        update_link(link, destination=link.destination, title=link.title, tags=link.tags)
        assert ShortLink.objects.get(pk=link.pk).updated_at == before

    @pytest.mark.parametrize('status', [LinkStatus.DELETED, LinkStatus.TAKEN_DOWN])
    def test_a_deleted_or_taken_down_link_cannot_be_edited(self, funded_user, status):
        link = make_link(funded_user, status=status)
        with pytest.raises(ValidationError):
            update_link(link, title='x')

    def test_a_disabled_link_can_be_edited(self, funded_user):
        link = make_link(funded_user, status=LinkStatus.DISABLED)
        update_link(link, title='x')
        link.refresh_from_db()
        assert link.title == 'x'

    def test_a_new_destination_asks_for_a_title_only_when_there_is_none(
        self, funded_user, django_capture_on_commit_callbacks
    ):
        untitled = make_link(funded_user)
        titled = make_link(funded_user, title='Mine')
        with mock.patch.object(links_module, 'fetch_title') as task:
            with django_capture_on_commit_callbacks(execute=True):
                update_link(untitled, destination='https://example.com/changed')
                update_link(titled, destination='https://example.com/changed')
                update_link(untitled, title='')  # nothing changed: no fetch
        task.enqueue.assert_called_once_with(str(untitled.pk))

    def test_unset_is_not_none(self):
        assert UNSET is not None
        assert repr(UNSET) == 'UNSET'


class TestEnableDisable:
    def test_disable_then_enable(self, funded_user):
        link = new(funded_user)
        assert set_enabled(link, False) is True
        link.refresh_from_db()
        assert link.status == LinkStatus.DISABLED
        assert set_enabled(link, True) is True
        link.refresh_from_db()
        assert link.status == LinkStatus.ACTIVE
        assert balance(funded_user) == 10

    def test_repeating_is_a_no_op(self, funded_user):
        link = new(funded_user)
        assert set_enabled(link, True) is False
        set_enabled(link, False)
        assert set_enabled(link, False) is False

    def test_enabling_beyond_the_allowance_pays_once_per_link(self, funded_user):
        a, _b, c = (new(funded_user) for _ in range(3))  # c is the paid extra one
        assert balance(funded_user) == 9
        # a fits in the free allowance again once it is off and on: still free.
        set_enabled(a, False)
        set_enabled(a, True)
        assert balance(funded_user) == 9
        # c paid its way in, so toggling it is free too.
        set_enabled(c, False)
        set_enabled(c, True)
        assert balance(funded_user) == 9

        d = make_link(funded_user, status=LinkStatus.DISABLED)  # never paid
        assert quote_enable(d).total == 1
        assert set_enabled(d, True) is True
        assert balance(funded_user) == 8
        set_enabled(d, False)
        set_enabled(d, True)
        assert balance(funded_user) == 8

    def test_a_paid_link_does_not_use_up_a_free_slot(self, funded_user):
        a, b, c = (new(funded_user) for _ in range(3))  # c paid
        set_enabled(c, False)
        # Two unpaid links (a, b) fill the allowance: a new one costs again.
        assert quote_create(funded_user, None).total == 1
        set_enabled(a, False)
        # Now only b is unpaid-active: a new link is free, and c (paid) can come back free.
        assert not quote_create(funded_user, None)
        assert not quote_enable(c)
        assert b.pk

    def test_enabling_without_credits_leaves_it_disabled(self, user, prices):
        make_link(user)
        make_link(user)
        link = make_link(user, status=LinkStatus.DISABLED)
        with pytest.raises(InsufficientCreditsError):
            set_enabled(link, True)
        link.refresh_from_db()
        assert link.status == LinkStatus.DISABLED
        assert link.extra_link_paid_at is None

    def test_a_disabled_link_already_counts_so_enabling_needs_no_cap_check(self, funded_user):
        for _ in range(4):
            make_link(funded_user)
        link = make_link(funded_user, status=LinkStatus.DISABLED)  # the 5th and last slot
        assert set_enabled(link, True) is True

    @pytest.mark.parametrize('status', [LinkStatus.DELETED, LinkStatus.TAKEN_DOWN])
    def test_a_deleted_or_taken_down_link_cannot_be_toggled(self, funded_user, status):
        link = make_link(funded_user, status=status)
        for enabled in (True, False):
            with pytest.raises(ValidationError):
                set_enabled(link, enabled)

    def test_quote_enable(self, funded_user):
        a, b = new(funded_user), new(funded_user)
        disabled = make_link(funded_user, status=LinkStatus.DISABLED)
        assert quote_enable(disabled).total == 1
        assert not quote_enable(a)  # active: nothing to pay
        set_enabled(b, False)
        assert not quote_enable(b)  # one free slot is open again
        paid = make_link(funded_user, status=LinkStatus.DISABLED, extra_link_paid_at=timezone.now())
        make_link(funded_user)
        assert not quote_enable(paid)

    def test_enable_costs_reads_the_page_in_one_go(self, funded_user):
        make_link(funded_user)
        make_link(funded_user)
        unpaid = make_link(funded_user, status=LinkStatus.DISABLED)
        paid = make_link(funded_user, status=LinkStatus.DISABLED, extra_link_paid_at=timezone.now())
        active = make_link(funded_user)
        costs = enable_costs(funded_user, [unpaid, paid, active])
        assert costs == {unpaid.pk: 1, paid.pk: 0, active.pk: 0}


class TestDelete:
    def test_it_is_soft(self, funded_user):
        link = new(funded_user, alias='keepme')
        assert delete_link(link) is True
        row = ShortLink.objects.get(pk=link.pk)
        assert row.status == LinkStatus.DELETED
        assert row.deleted_at is not None
        assert row.code_key == 'keepme'

    def test_the_code_is_never_reused(self, funded_user, other_user):
        add_credits(other_user, 5, description='x', source='test')
        link = new(funded_user, alias='printed')
        delete_link(link)
        with pytest.raises(AliasTakenError):
            new(other_user, alias='PRINTED')
        with pytest.raises(AliasTakenError):
            new(funded_user, alias='printed')

    def test_deleting_twice_is_a_no_op(self, funded_user):
        link = new(funded_user)
        delete_link(link)
        assert delete_link(link) is False

    def test_a_disabled_link_can_be_deleted(self, funded_user):
        link = make_link(funded_user, status=LinkStatus.DISABLED)
        assert delete_link(link) is True

    def test_a_taken_down_link_stays_taken_down(self, funded_user):
        link = make_link(funded_user, status=LinkStatus.TAKEN_DOWN)
        with pytest.raises(ValidationError):
            delete_link(link)
        link.refresh_from_db()
        assert link.status == LinkStatus.TAKEN_DOWN

    def test_it_frees_the_slot_for_the_allowance(self, funded_user):
        a, _b = new(funded_user), new(funded_user)
        delete_link(a)
        new(funded_user)
        assert balance(funded_user) == 10


class TestAvailability:
    def test_an_active_link_is_available(self, user):
        assert is_available(make_link(user))

    def test_every_other_state_is_not(self, user):
        now = timezone.now()
        cases = [
            make_link(user, status=LinkStatus.DISABLED),
            make_link(user, status=LinkStatus.DELETED),
            make_link(user, status=LinkStatus.TAKEN_DOWN),
            make_link(user, held_for_review_at=now),
            expired(user),
            make_link(user, expires_at=now),
            make_link(user, max_clicks=3, click_count=3),
            make_link(user, max_clicks=3, click_count=4),
        ]
        assert not any(is_available(link) for link in cases)

    def test_a_password_does_not_make_a_link_unavailable(self, user):
        """The visitor is asked for it (`views.password`); the link itself still works."""
        assert is_available(make_link(user, password_hash='pbkdf2_sha256$x'))

    def test_a_future_expiry_and_an_unreached_cap_are_available(self, user):
        link = make_link(
            user, expires_at=timezone.now() + timedelta(days=1), max_clicks=3, click_count=2
        )
        assert is_available(link)

    def test_display_state(self, user):
        now = timezone.now()
        assert display_state(make_link(user)) == 'active'
        assert display_state(make_link(user, status=LinkStatus.DISABLED)) == 'disabled'
        assert display_state(make_link(user, status=LinkStatus.TAKEN_DOWN)) == 'taken_down'
        assert display_state(make_link(user, status=LinkStatus.DELETED)) == 'deleted'
        assert display_state(make_link(user, held_for_review_at=now)) == 'held'
        assert display_state(expired(user)) == 'expired'
        assert display_state(make_link(user, max_clicks=1, click_count=1)) == 'capped'
        # Expiry wins over the cap, a status of the owner's over both.
        assert display_state(expired(user, max_clicks=1, click_count=1)) == 'expired'
        assert display_state(expired(user, status=LinkStatus.DISABLED)) == 'disabled'


class TestTags:
    def test_split_lowered_deduplicated_in_order(self):
        assert normalize_tags('b, A,B ,c') == ['b', 'a', 'c']

    def test_accepts_a_list_or_nothing(self):
        assert normalize_tags(['X', 'y']) == ['x', 'y']
        assert normalize_tags(None) == []
        assert normalize_tags('') == []
        assert normalize_tags(' , ,') == []

    def test_spaces_become_hyphens(self):
        assert normalize_tags('big   sale') == ['big-sale']

    @pytest.mark.parametrize('tag', ['über', 'a/b', 'a"b', '#x', 'a.b', '-x'])
    def test_bad_tags(self, tag):
        with pytest.raises(ValidationError):
            normalize_tags(tag)


def test_short_url():
    assert short_url('abc') == 'https://app.example.test/s/abc'


def test_short_url_ignores_a_trailing_slash_on_the_base(settings):
    settings.BASE_URL = 'https://app.example.test/'
    assert short_url('abc') == 'https://app.example.test/s/abc'


def test_the_users_of_two_accounts_do_not_interfere(funded_user):
    other = make_user()
    add_credits(other, 5, description='x', source='test')
    new(other, alias='theirs')
    new(funded_user, alias='mine')
    assert balance(other) == 4
    assert balance(funded_user) == 9


class TestExpectedCost:
    """The cost is re-checked inside the transaction that charges, so a total confirmed against
    an out-of-date quote never charges (the free allowance is 2 active links)."""

    def test_a_confirmed_total_that_still_holds_creates_and_charges(self, funded_user):
        make_link(funded_user)
        make_link(funded_user)
        new_link = create_link(
            funded_user, LinkInput(destination='https://example.com/x'), expected_cost=1
        )
        assert new_link.extra_link_paid_at is not None
        assert balance(funded_user) == 9

    def test_none_accepts_any_cost(self, funded_user):
        make_link(funded_user)
        make_link(funded_user)
        create_link(funded_user, LinkInput(destination='https://example.com/x', alias='Any001'))
        assert balance(funded_user) == 8

    def test_free_when_quoted_but_the_allowance_ran_out_before_the_charge(self, funded_user):
        make_link(funded_user)
        assert quote_create(funded_user, None).total == 0
        make_link(funded_user)  # another tab used the last free slot
        with pytest.raises(CostChangedError) as caught:
            create_link(
                funded_user, LinkInput(destination='https://example.com/x'), expected_cost=0
            )
        assert caught.value.quote.total == 1
        assert balance(funded_user) == 10
        assert ShortLink.objects.filter(owner=funded_user).count() == 2

    def test_a_price_change_is_caught_too(self, funded_user, prices):
        prices.custom_alias_price = 4
        prices.save()
        with pytest.raises(CostChangedError) as caught:
            create_link(
                funded_user,
                LinkInput(destination='https://example.com/x', alias='Pri001'),
                expected_cost=1,
            )
        assert caught.value.quote.total == 4
        assert not ShortLink.objects.exists()
        assert balance(funded_user) == 10

    def test_enabling_whose_cost_appeared_since_the_quote(self, funded_user):
        make_link(funded_user)
        link = make_link(funded_user, status=LinkStatus.DISABLED)
        assert quote_enable(link).total == 0
        make_link(funded_user)  # the free allowance is gone now
        with pytest.raises(CostChangedError) as caught:
            set_enabled(link, True, expected_cost=0)
        assert caught.value.quote.total == 1
        link.refresh_from_db()
        assert link.status == LinkStatus.DISABLED and link.extra_link_paid_at is None
        assert balance(funded_user) == 10

    def test_enabling_with_the_confirmed_cost(self, funded_user):
        make_link(funded_user)
        make_link(funded_user)
        link = make_link(funded_user, status=LinkStatus.DISABLED)
        assert set_enabled(link, True, expected_cost=1) is True
        assert balance(funded_user) == 9

    def test_a_paid_link_is_free_to_enable_again(self, funded_user):
        make_link(funded_user)
        make_link(funded_user)
        link = make_link(funded_user, status=LinkStatus.DISABLED, extra_link_paid_at=timezone.now())
        assert set_enabled(link, True, expected_cost=0) is True
        assert balance(funded_user) == 10


class TestUpdateAgainstTheLockedRow:
    def test_an_edit_that_matches_the_fresh_row_writes_nothing(self, funded_user):
        link = make_link(funded_user, destination='https://example.com/old', title='T')
        stale = ShortLink.objects.get(pk=link.pk)
        ShortLink.objects.filter(pk=link.pk).update(destination='https://example.com/new')
        before = ShortLink.objects.get(pk=link.pk).updated_at
        update_link(stale, destination='https://example.com/new')
        assert ShortLink.objects.get(pk=link.pk).updated_at == before
        assert stale.destination == 'https://example.com/new'

    def test_the_fetch_decision_uses_the_fresh_title(
        self, funded_user, django_capture_on_commit_callbacks
    ):
        link = make_link(funded_user)  # untitled in the caller's copy...
        stale = ShortLink.objects.get(pk=link.pk)
        ShortLink.objects.filter(pk=link.pk).update(title='Typed elsewhere')  # ...titled now
        with mock.patch.object(links_module, 'fetch_title') as task:
            with django_capture_on_commit_callbacks(execute=True):
                update_link(stale, destination='https://example.com/changed')
        task.enqueue.assert_not_called()
        assert stale.title == 'Typed elsewhere'


class TestPasswordCharges:
    """The password extra: charged once per link, whenever a password is first set."""

    def test_creating_with_a_password_stores_a_hash_and_charges_once(self, funded_user):
        from django.contrib.auth.hashers import check_password

        link = new(funded_user, password='pw1')
        assert check_password('pw1', link.password_hash) and 'pw1' != link.password_hash
        assert link.password_paid_at is not None
        assert balance(funded_user) == 9

    def test_the_cost_is_confirmed_inside_the_transaction(self, funded_user):
        with pytest.raises(CostChangedError) as exc:
            create_link(
                funded_user,
                LinkInput(destination='https://example.com', password='pw1'),
                expected_cost=0,
            )
        assert exc.value.quote.total == 1
        assert balance(funded_user) == 10 and not ShortLink.objects.exists()

    def test_alias_password_and_extra_link_add_up(self, funded_user):
        create_link(funded_user, LinkInput(destination='https://a.example.com'))
        create_link(funded_user, LinkInput(destination='https://b.example.com'))
        quote = quote_create(funded_user, 'Sale', has_password=True)
        assert quote.total == 3
        link = create_link(
            funded_user,
            LinkInput(destination='https://c.example.com', alias='Sale', password='pw'),
            expected_cost=3,
        )
        assert balance(funded_user) == 7
        assert link.alias_paid_at and link.password_paid_at and link.extra_link_paid_at

    def test_a_refused_charge_rolls_the_whole_creation_back(self, user, prices):
        add_credits(user, 1, description='t', source='test')
        with pytest.raises(InsufficientCreditsError):
            create_link(
                user, LinkInput(destination='https://example.com', alias='Sale', password='pw')
            )
        assert not ShortLink.objects.exists()
        assert balance(user) == 1

    def test_setting_one_later_charges_once_then_it_is_free(self, funded_user):
        link = make_link(funded_user)
        assert quote_update(link, password='x').total == 1
        update_link(link, password='first', expected_cost=1)
        assert balance(funded_user) == 9
        assert quote_update(link, password='second').total == 0
        update_link(link, password='second', expected_cost=0)
        update_link(link, password=None)  # removed...
        update_link(link, password='third', expected_cost=0)  # ...and set again: still free
        assert balance(funded_user) == 9
        link.refresh_from_db()
        assert link.password_hash

    def test_a_wrong_expected_cost_changes_nothing(self, funded_user):
        link = make_link(funded_user)
        with pytest.raises(CostChangedError):
            update_link(link, title='New', password='first', expected_cost=0)
        link.refresh_from_db()
        assert not link.password_hash and link.title != 'New'
        assert balance(funded_user) == 10

    def test_an_edit_without_a_password_has_nothing_to_confirm(self, funded_user):
        link = make_link(funded_user)
        assert quote_update(link).total == 0
        update_link(link, title='New', expected_cost=0)
        assert balance(funded_user) == 10

    def test_clearing_a_password_that_was_never_set_is_a_no_op(self, funded_user):
        link = make_link(funded_user)
        update_link(link, password=None, expected_cost=0)
        assert link.password_hash is None and balance(funded_user) == 10

    def test_insufficient_credits_roll_the_edit_back(self, user, prices):
        link = make_link(user)
        with pytest.raises(InsufficientCreditsError):
            update_link(link, title='New', password='first')
        link.refresh_from_db()
        assert not link.password_hash and link.title != 'New' and link.password_paid_at is None

    def test_a_bad_password_is_a_field_error_before_anything_is_charged(self, funded_user):
        link = make_link(funded_user)
        with pytest.raises(ValidationError) as exc:
            update_link(link, password='   ')
        assert 'password' in exc.value.error_dict
        assert balance(funded_user) == 10

    def test_the_bot_filter_can_be_changed(self, funded_user):
        link = make_link(funded_user)
        update_link(link, filter_bots=False)
        link.refresh_from_db()
        assert link.filter_bots is False

    def test_a_new_link_can_start_with_bots_counted(self, funded_user):
        link = create_link(
            funded_user, LinkInput(destination='https://example.com', filter_bots=False)
        )
        assert link.filter_bots is False


class TestAnalyticsCharge:
    def test_it_is_charged_once(self, funded_user):
        link = make_link(funded_user)
        assert not analytics_unlocked(link)
        assert quote_analytics(link).total == 1
        assert enable_analytics(link, expected_cost=1) is True
        assert analytics_unlocked(link) and quote_analytics(link).total == 0
        assert enable_analytics(link, expected_cost=0) is False
        assert balance(funded_user) == 9

    def test_a_different_total_than_confirmed_changes_nothing(self, funded_user):
        link = make_link(funded_user)
        with pytest.raises(CostChangedError) as exc:
            enable_analytics(link, expected_cost=5)
        assert exc.value.quote.total == 1
        assert not analytics_unlocked(link) and balance(funded_user) == 10

    def test_without_credits_it_stays_closed(self, user, prices):
        link = make_link(user)
        with pytest.raises(InsufficientCreditsError):
            enable_analytics(link)
        link.refresh_from_db()
        assert link.analytics_paid_at is None

    def test_a_price_of_zero_is_open_without_paying(self, funded_user, prices):
        prices.analytics_price = 0
        prices.save()
        link = make_link(funded_user)
        assert analytics_unlocked(link) and quote_analytics(link).total == 0
        enable_analytics(link, expected_cost=0)
        assert balance(funded_user) == 10

    def test_a_link_that_cannot_be_changed_cannot_buy_it(self, funded_user):
        link = make_link(funded_user, status=LinkStatus.TAKEN_DOWN)
        with pytest.raises(ValidationError):
            enable_analytics(link)
