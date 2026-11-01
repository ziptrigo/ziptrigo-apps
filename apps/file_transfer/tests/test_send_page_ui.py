"""The send pages' drop zone and picker buttons: `resumable_upload.js`'s page scripts look these
elements up by id with no null checks, so a rename would break uploads silently in the browser."""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

REQUIRED_IDS = ['drop-zone', 'pick-files-btn', 'pick-folder-btn', 'file-input', 'folder-input']


def _assert_upload_controls(html: str) -> None:
    for element_id in REQUIRED_IDS:
        assert f'id="{element_id}"' in html, element_id
    assert 'webkitdirectory' in html
    assert 'initDropZone(dropZone' in html
    # `initDropZone` lives in core's shared script, which must load before the page's own.
    assert 'core/js/drop_zone.js' in html
    assert html.index('core/js/drop_zone.js') < html.index('file_transfer/js/resumable_upload.js')


def test_send_page_has_drop_zone_and_pickers(client, funded_user):
    client.force_login(funded_user)
    html = client.get(reverse('file_transfer:send')).content.decode()
    _assert_upload_controls(html)


def test_anonymous_send_page_has_drop_zone_and_pickers(client, anon_enabled):
    html = client.get(reverse('file_transfer:anon-send')).content.decode()
    _assert_upload_controls(html)


def test_upload_script_reports_problems_inline_not_with_alert():
    """A network error used to be a blocking `alert()` while the row only said "Paused" (#224).
    Problems now go on the row (or one page-level region) as a `role="alert"` line, which both
    send pages get because they share this script."""
    script = (
        Path(settings.PROJECT_ROOT)
        / 'apps/file_transfer/static/file_transfer/js/resumable_upload.js'
    ).read_text()
    assert not re.search(r'(?<![\w.])alert\(', script)
    assert script.count("setAttribute('role', 'alert')") == 2  # the row line and the page region
    assert "'Upload interrupted by a network error." in script
