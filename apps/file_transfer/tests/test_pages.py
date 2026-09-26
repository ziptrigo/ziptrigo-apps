import pytest
from django.urls import reverse

from apps.accounts.models import User

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture()
def user() -> User:
    return User.objects.create_user(email='transfer@example.com', password='password123')


def test_index_redirects_anonymous_user_to_login(client):
    response = client.get(reverse('file_transfer:index'))

    assert response.status_code == 302
    assert response['Location'].startswith(reverse('accounts:login'))


def test_index_renders_for_logged_in_user(client, user: User):
    client.force_login(user)

    response = client.get(reverse('file_transfer:index'))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'File Transfer' in content
    assert reverse('file_transfer:transfer-list') in content


def test_transfer_list_is_a_partial(client, user: User):
    client.force_login(user)

    response = client.get(reverse('file_transfer:transfer-list'), HTTP_HX_REQUEST='true')

    assert response.status_code == 200
    content = response.content.decode()
    assert 'No transfers yet.' in content
    assert '<html' not in content
