"""The footer shared by every page."""

import re

import pytest
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_footer_links_to_the_feedback_page(client):
    html = client.get(reverse('core:home')).content.decode()

    assert reverse('feedback:submit') == '/feedback/'
    assert 'href="/feedback/"' in html
    [anchor] = re.findall(r'<a [^>]*href="/feedback/"[^>]*>', html)
    assert 'aria-current' not in anchor


def test_footer_link_is_marked_current_on_the_feedback_page(client, user):
    client.force_login(user)

    html = client.get(reverse('feedback:submit')).content.decode()

    assert 'href="/feedback/" class="hover:text-sage-300" aria-current="page"' in html


def test_footer_has_no_placeholder_links(client):
    html = client.get(reverse('core:home')).content.decode()

    assert 'href="#"' not in html


def test_skip_link_points_at_the_main_element(client):
    html = client.get(reverse('core:home')).content.decode()

    assert html.index('href="#main"') < html.index('<header')
    assert '<main id="main" tabindex="-1"' in html
