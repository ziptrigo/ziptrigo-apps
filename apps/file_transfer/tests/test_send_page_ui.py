"""The send pages' drop zone and picker buttons: `resumable_upload.js`'s page scripts look these
elements up by id with no null checks, so a rename would break uploads silently in the browser."""

import pytest
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

REQUIRED_IDS = ['drop-zone', 'pick-files-btn', 'pick-folder-btn', 'file-input', 'folder-input']


def _assert_upload_controls(html: str) -> None:
    for element_id in REQUIRED_IDS:
        assert f'id="{element_id}"' in html, element_id
    assert 'webkitdirectory' in html
    assert 'initDropZone(dropZone' in html


def test_send_page_has_drop_zone_and_pickers(client, funded_user):
    client.force_login(funded_user)
    html = client.get(reverse('file_transfer:send')).content.decode()
    _assert_upload_controls(html)


def test_anonymous_send_page_has_drop_zone_and_pickers(client, anon_enabled):
    html = client.get(reverse('file_transfer:anon-send')).content.decode()
    _assert_upload_controls(html)
