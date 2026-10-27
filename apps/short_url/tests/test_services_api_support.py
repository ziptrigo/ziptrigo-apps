"""The service additions the API needed (phase 4): `check_new_link` (a dry run of a create),
`credits_spent` on the link, and `filters.filter_links` shared with the dashboard."""

import pytest
from django.utils import timezone

from apps.accounts.models import User

from ..models import LinkStatus, ShortLink
from ..services import (
    AliasTakenError,
    LimitReachedError,
    LinkInput,
    OwnerInactiveError,
    check_new_link,
    create_link,
    set_enabled,
    update_link,
)
from ..services.filters import filter_links, state_filter
from .conftest import balance
from .factories import create_link as make_link

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


class TestCheckNewLink:
    def test_it_quotes_without_saving_or_charging(self, funded_user):
        quote = check_new_link(
            funded_user, LinkInput('https://example.com', alias='dryrun', password='pw')
        )

        assert quote.total == 2
        assert not ShortLink.objects.exists()
        assert balance(funded_user) == 10

    def test_it_quotes_an_extra_link_beyond_the_allowance(self, funded_user):
        make_link(funded_user)
        make_link(funded_user)

        assert check_new_link(funded_user, LinkInput('https://example.com')).total == 1

    def test_it_refuses_what_create_would_refuse(self, funded_user, other_user):
        make_link(other_user, code='Taken')

        with pytest.raises(AliasTakenError):
            check_new_link(funded_user, LinkInput('https://example.com', alias='taken'))

    def test_it_refuses_an_owner_who_is_not_active(self, funded_user):
        User.objects.filter(pk=funded_user.pk).update(status=User.STATUS_INACTIVE)

        with pytest.raises(OwnerInactiveError):
            check_new_link(funded_user, LinkInput('https://example.com'))

    def test_it_refuses_past_the_cap(self, funded_user, prices):
        for _ in range(prices.max_active_links):
            make_link(funded_user, status=LinkStatus.DISABLED)

        with pytest.raises(LimitReachedError):
            check_new_link(funded_user, LinkInput('https://example.com'))

    def test_it_does_not_hash_the_password_but_checks_it(self, funded_user):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            check_new_link(funded_user, LinkInput('https://example.com', password='   '))


class TestCreditsSpent:
    def test_create_leaves_the_total_on_the_link(self, funded_user):
        link = create_link(funded_user, LinkInput('https://example.com', alias='spent1'))

        assert link.credits_spent == 1

    def test_a_free_create_leaves_zero(self, funded_user):
        assert create_link(funded_user, LinkInput('https://example.com')).credits_spent == 0

    def test_update_and_set_enabled_reset_it(self, funded_user):
        link = create_link(funded_user, LinkInput('https://example.com', alias='spent2'))

        update_link(link, title='x')
        assert link.credits_spent == 0
        update_link(link, password='pw')
        assert link.credits_spent == 1
        set_enabled(link, False)
        assert link.credits_spent == 0


class TestFilterLinks:
    def test_an_unknown_state_is_a_value_error(self):
        with pytest.raises(ValueError):
            state_filter('sleeping', timezone.now())

    def test_the_default_hides_deleted_links_and_orders_newest_first(self, funded_user):
        make_link(funded_user, code='aaa0001')
        make_link(funded_user, code='bbb0001')
        make_link(funded_user, code='ccc0001', status=LinkStatus.DELETED)

        assert [link.code for link in filter_links(funded_user)] == ['bbb0001', 'aaa0001']
