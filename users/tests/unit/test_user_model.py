import pytest
from django.utils import timezone
from src.users.models import User

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_user_mark_deleted_sets_status_and_timestamp(regular_user: User):
    assert regular_user.status == User.STATUS_ACTIVE
    assert regular_user.deleted_at is None

    regular_user.mark_deleted()
    regular_user.refresh_from_db()

    assert regular_user.status == User.STATUS_DELETED
    assert regular_user.deleted_at is not None
    assert regular_user.deleted_at <= timezone.now()


def test_user_deactivate_sets_status_reason_and_timestamp(regular_user: User):
    regular_user.deactivate('policy violation')
    regular_user.refresh_from_db()

    assert regular_user.status == User.STATUS_INACTIVE
    assert regular_user.inactive_at is not None
    assert regular_user.inactive_reason == 'policy violation'


def test_user_reactivate_clears_inactive_fields(regular_user: User):
    regular_user.deactivate('temp lock')

    regular_user.reactivate()
    regular_user.refresh_from_db()

    assert regular_user.status == User.STATUS_ACTIVE
    assert regular_user.inactive_at is None
    assert regular_user.inactive_reason == ''
