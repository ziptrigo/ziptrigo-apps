"""The template helpers behind the notification lists (issue #169)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.templatetags.notification_tags import (
    BADGE_CAP,
    ago,
    badge_text,
    source_icon,
    source_label,
)

pytestmark = pytest.mark.unit


def test_badge_text_caps_large_counts():
    assert badge_text(1) == '1'
    assert badge_text(BADGE_CAP) == str(BADGE_CAP)
    assert badge_text(BADGE_CAP + 1) == f'{BADGE_CAP}+'


def test_ago_reads_like_a_chat_app():
    now = timezone.now()

    assert ago(now - timedelta(seconds=20)) == 'just now'
    assert ago(now - timedelta(minutes=5, seconds=10)) == '5\xa0minutes ago'
    assert ago(now - timedelta(hours=3, minutes=40)) == '3\xa0hours ago'
    assert ago(now - timedelta(days=2, hours=5)) == '2\xa0days ago'


def test_source_label_uses_the_products_name_or_makes_the_label_readable():
    assert source_label('file_transfer') == 'File Transfer'
    assert source_label('some_new_app') == 'Some new app'


def test_source_icon_falls_back_to_a_bell():
    assert source_icon('file_transfer') == 'fas fa-paper-plane'
    assert source_icon('some_new_app') == 'fas fa-bell'
