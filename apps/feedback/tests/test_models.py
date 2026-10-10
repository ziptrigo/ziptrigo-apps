import pytest

from ..models import Feedback, FeedbackStatus

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_status_defaults_to_new(user):
    feedback = Feedback.objects.create(created_by=user, description='Hello')

    assert feedback.status == FeedbackStatus.NEW
    assert feedback.created_at is not None
    assert feedback.updated_at is not None


def test_newest_first(user):
    older = Feedback.objects.create(created_by=user, description='older')
    newer = Feedback.objects.create(created_by=user, description='newer')

    assert list(Feedback.objects.all()) == [newer, older]


def test_str(user):
    feedback = Feedback.objects.create(created_by=user, description='Hello')

    assert str(feedback) == f'Feedback {feedback.id} (new)'


def test_survives_deleting_the_user(user):
    feedback = Feedback.objects.create(created_by=user, description='Hello')

    user.delete()

    feedback.refresh_from_db()
    assert feedback.created_by is None
