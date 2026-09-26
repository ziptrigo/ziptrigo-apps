"""Fixtures shared by every app's tests."""

from datetime import UTC, datetime

import pytest

from apps.accounts.tests.factories import UserFactory


@pytest.fixture()
def api_client():
    """A Ninja test client for the site's single API (`config.api.api`)."""
    from ninja.testing import TestClient

    from config.api import api

    return TestClient(api)


@pytest.fixture()
def admin_user():
    return UserFactory(is_staff=True, is_superuser=True)


@pytest.fixture()
def regular_user():
    return UserFactory()


@pytest.fixture
def user(db):
    """A user with a confirmed email address."""
    from apps.accounts.models import User

    user = User.objects.create_user(
        email='testuser@example.com',
        password='testpass123',
        name='Test User',
    )
    user.email_confirmed = True
    user.email_confirmed_at = datetime.now(UTC)
    user.save()
    return user
