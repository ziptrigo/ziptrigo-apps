import pytest
from django.dispatch import receiver

from apps.billing.services import add_credits
from apps.billing.signals import credits_added

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_add_credits_sends_credits_added_signal_on_commit(user, django_capture_on_commit_callbacks):
    received = []

    @receiver(credits_added, dispatch_uid='test_add_credits_sends_credits_added_signal_on_commit')
    def _handler(sender, user, amount, **kwargs):
        received.append((user, amount))

    try:
        with django_capture_on_commit_callbacks(execute=True):
            add_credits(user, 5, description='test', source='test')
    finally:
        credits_added.disconnect(
            dispatch_uid='test_add_credits_sends_credits_added_signal_on_commit'
        )

    assert received == [(user, 5)]


def test_credits_added_signal_not_sent_before_commit(user, django_capture_on_commit_callbacks):
    received = []

    @receiver(credits_added, dispatch_uid='test_credits_added_signal_not_sent_before_commit')
    def _handler(sender, user, amount, **kwargs):
        received.append((user, amount))

    try:
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            add_credits(user, 5, description='test', source='test')
            assert received == []
        assert len(callbacks) == 1
    finally:
        credits_added.disconnect(dispatch_uid='test_credits_added_signal_not_sent_before_commit')
