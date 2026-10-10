"""The footer shared by every page."""

import pytest
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_footer_links_to_the_feedback_page(client):
    html = client.get(reverse('core:home')).content.decode()

    assert reverse('feedback:submit') == '/feedback/'
    assert 'href="/feedback/"' in html
    assert 'aria-current' not in html


def test_footer_link_is_marked_current_on_the_feedback_page(client, user):
    client.force_login(user)

    html = client.get(reverse('feedback:submit')).content.decode()

    assert 'href="/feedback/" class="hover:text-sage-300" aria-current="page"' in html
