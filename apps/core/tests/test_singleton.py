"""`load_singleton`, the shared `pk=1` loader behind `CoreSettings.load()` and friends."""

from unittest import mock

import pytest
from django.db import IntegrityError

from apps.core.models import CoreSettings
from apps.core.models.singleton import load_singleton

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_creates_the_row_on_first_use_and_reuses_it():
    first = load_singleton(CoreSettings)
    second = load_singleton(CoreSettings)

    assert first.pk == second.pk == 1
    assert CoreSettings.objects.count() == 1


def test_losing_the_insert_race_rereads_the_winners_row():
    winner = CoreSettings.objects.create(pk=1, email_verification_code_length=8)

    # The loser's `get_or_create` sees an empty table, then its insert collides with the winner's.
    with mock.patch.object(
        type(CoreSettings._default_manager), 'get_or_create', side_effect=IntegrityError
    ):
        loaded = load_singleton(CoreSettings)

    assert loaded.pk == winner.pk
    assert loaded.email_verification_code_length == 8
    assert CoreSettings.load().email_verification_code_length == 8
