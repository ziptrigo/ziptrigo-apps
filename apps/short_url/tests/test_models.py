import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.core.models.singleton import load_singleton

from ..models import LinkStatus, ShortLink, ShortUrlSettings
from .factories import create_link

pytestmark = pytest.mark.django_db


class TestShortUrlSettings:
    def test_defaults(self):
        row = ShortUrlSettings.load()
        assert row.pk == 1
        assert (
            row.custom_alias_price,
            row.password_price,
            row.analytics_price,
            row.extra_link_price,
        ) == (1, 1, 1, 1)
        assert (row.free_active_links, row.max_active_links) == (25, 500)
        assert row.click_event_retention_days == 90
        assert row.auto_hold_report_threshold == 0

    def test_it_is_a_singleton(self):
        first = ShortUrlSettings.load()
        first.custom_alias_price = 9
        first.save()
        assert ShortUrlSettings.load().custom_alias_price == 9
        assert ShortUrlSettings.objects.count() == 1
        assert load_singleton(ShortUrlSettings).pk == 1

    def test_free_cannot_exceed_the_cap(self):
        row = ShortUrlSettings(free_active_links=10, max_active_links=5)
        with pytest.raises(ValidationError) as caught:
            row.clean()
        assert 'free_active_links' in caught.value.message_dict

    def test_free_equal_to_the_cap_is_fine(self):
        ShortUrlSettings(free_active_links=5, max_active_links=5).clean()

    def test_a_zero_cap_and_zero_retention_are_refused(self):
        row = ShortUrlSettings(
            max_active_links=0, free_active_links=0, click_event_retention_days=0
        )
        with pytest.raises(ValidationError) as caught:
            row.full_clean()
        assert {'max_active_links', 'click_event_retention_days'} <= set(caught.value.message_dict)

    def test_str(self):
        assert str(ShortUrlSettings.load()) == 'Short URL settings'


class TestShortLink:
    def test_defaults(self, user):
        link = create_link(user)
        assert link.status == LinkStatus.ACTIVE
        assert link.tags == []
        assert link.filter_bots is True
        assert (link.click_count, link.bot_click_count) == (0, 0)
        assert link.password_hash is None
        assert link.held_for_review_at is None
        assert link.alias_paid_at is link.password_paid_at is None
        assert link.analytics_paid_at is link.extra_link_paid_at is None
        assert link.deleted_at is None
        assert link.created_at and link.updated_at

    def test_the_key_is_unique_across_every_case(self, user):
        create_link(user, code='Sale')
        with pytest.raises(IntegrityError), transaction.atomic():
            ShortLink.objects.create(
                owner=user, code='SALE', code_key='sale', destination='https://x.example'
            )

    def test_the_owner_s_links_go_with_them(self, user):
        create_link(user)
        user.delete()
        assert not ShortLink.objects.exists()

    def test_str_is_the_code(self, user):
        assert str(create_link(user, code='Strc01')) == 'Strc01'

    def test_newest_first(self, user):
        first = create_link(user)
        second = create_link(user)
        assert list(ShortLink.objects.all()) == [second, first]
