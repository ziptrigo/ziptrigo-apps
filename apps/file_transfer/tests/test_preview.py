"""File previews on the public download page (issue #311): what is previewable (decided from the
file's bytes at upload), the preview endpoint and its gates, and the page's hint and buttons."""

import io
import json

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from .. import services
from ..models import DownloadEvent, TransferFile, TransferStatus
from ..services import password as password_service
from ..services import preview, request_upload
from ..services import requests as requests_service
from ..services.takedown import take_down_transfer

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 32
PDF = b'%PDF-1.7\n' + b'\0' * 32
MP4 = b'\0\0\0\x18ftypisom\0\0\2\0isomiso2'
HTML = b'<!DOCTYPE html><html><body><script>alert(1)</script></body></html>'


def _add(transfer, fake_storage, name, content, *, size=None):
    """A finished upload of `content`, through the real completion path (so the type is sniffed)."""
    size = size if size is not None else len(content)
    file = services.add_file(transfer, name, size, storage=fake_storage)
    fake_storage.seed_object(file.storage_key, size, content)
    services.complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'etag-1'}], storage=fake_storage)
    file.refresh_from_db()
    return file


def _active(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


def _url(transfer, file):
    return reverse('t:preview', args=[transfer.slug, file.id])


# -- Sniffing at upload --


class TestDetectPreviewType:
    @pytest.mark.parametrize(
        ('head', 'name', 'expected'),
        [
            (PNG, 'a.png', 'image/png'),
            (PNG, 'renamed.txt', 'image/png'),
            (PDF, 'a.pdf', 'application/pdf'),
            (MP4, 'a.mp4', 'video/mp4'),
            (b'hello\n', 'a.txt', 'text/plain'),
            (b'hello\n', 'photo.png', 'text/plain'),
            (HTML, 'photo.png', 'text/plain'),
            (HTML, 'page.html', 'text/plain'),
            (b'# Title\n', 'notes.md', 'text/markdown'),
            (b'# Title\n', 'NOTES.MARKDOWN', 'text/markdown'),
            (b'\0\0\0\0', 'a.png', ''),
            (b'MZ\x90\0\x03', 'tool.md', ''),
            (b'', 'a.txt', ''),
        ],
    )
    def test_the_bytes_decide_the_name_only_picks_markdown(self, head, name, expected):
        assert preview.detect_preview_type(head, name) == expected

    def test_a_binary_file_called_md_is_not_markdown(self):
        assert preview.detect_preview_type(b'\x00\x01\x02\xff', 'x.md') == ''

    def test_the_type_is_stored_when_the_upload_completes(self, draft_transfer, fake_storage):
        png = _add(draft_transfer, fake_storage, 'x.bin', PNG)
        text = _add(draft_transfer, fake_storage, 'x.png', b'not an image')
        zeros = _add(draft_transfer, fake_storage, 'x.pdf', b'\0' * 50)
        assert (png.preview_type, text.preview_type, zeros.preview_type) == (
            'image/png',
            'text/plain',
            '',
        )

    def test_a_failing_read_is_no_preview_not_a_failed_upload(
        self, draft_transfer, fake_storage, monkeypatch
    ):
        def boom(*args, **kwargs):
            raise RuntimeError('S3 is down')

        monkeypatch.setattr(fake_storage, 'get_object_range', boom)
        assert _add(draft_transfer, fake_storage, 'a.png', PNG).preview_type == ''

    def test_a_file_request_upload_stores_the_type_too(
        self, make_request, upload_to_request, anon_uploader, fake_storage
    ):
        transfer = make_request()
        file = request_upload.add_file(
            transfer, anon_uploader, 'p.jpg', len(PNG), ip='9.9.9.9', storage=fake_storage
        )
        fake_storage.seed_object(file.storage_key, len(PNG), PNG)
        done = request_upload.complete_file(
            transfer,
            file,
            [{'PartNumber': 1, 'ETag': 'e'}],
            anon_uploader,
            ip='9.9.9.9',
            storage=fake_storage,
        )
        assert done.preview_type == 'image/png'
        assert TransferFile.objects.get(pk=file.pk).preview_type == 'image/png'


# -- The endpoint --


class TestPreviewEndpoint:
    def test_an_image_redirects_to_a_presigned_inline_url_of_its_sniffed_type(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(draft_transfer, fake_storage, 'misnamed.txt', PNG)
        _active(draft_transfer)

        response = client.get(_url(draft_transfer, file))

        assert response.status_code == 302
        assert fake_storage.get_url_calls[-1] == (file.storage_key, 'inline', 'image/png')
        assert response['Cache-Control'] == 'no-store, private'
        assert response['X-Content-Type-Options'] == 'nosniff'

    @pytest.mark.parametrize(
        ('name', 'content', 'content_type'),
        [('a.pdf', PDF, 'application/pdf'), ('a.mp4', MP4, 'video/mp4')],
    )
    def test_pdf_and_video_redirect_inline(
        self, client, draft_transfer, fake_storage, name, content, content_type
    ):
        file = _add(draft_transfer, fake_storage, name, content)
        _active(draft_transfer)
        assert client.get(_url(draft_transfer, file)).status_code == 302
        assert fake_storage.get_url_calls[-1][1:] == ('inline', content_type)

    def test_a_preview_is_not_a_download(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'a.png', PNG)
        _active(draft_transfer, max_downloads=1)

        for _ in range(3):
            assert client.get(_url(draft_transfer, file)).status_code == 302

        assert not DownloadEvent.objects.exists()
        draft_transfer.refresh_from_db()
        assert draft_transfer.status == TransferStatus.ACTIVE
        # The one real download still works.
        download = client.get(reverse('t:download-file', args=[draft_transfer.slug, file.id]))
        assert download.status_code == 302
        assert DownloadEvent.objects.count() == 1

    def test_html_renamed_as_an_image_is_text_never_an_image_or_a_page(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(draft_transfer, fake_storage, 'photo.png', HTML)
        _active(draft_transfer)

        response = client.get(_url(draft_transfer, file))

        assert response.status_code == 200
        assert response['Content-Type'] == 'text/plain; charset=utf-8'
        assert response['X-Content-Type-Options'] == 'nosniff'
        assert 'sandbox' in response['Content-Security-Policy']
        assert response.content == HTML
        assert fake_storage.get_url_calls == []

    def test_an_html_file_is_only_ever_raw_text(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'page.html', HTML)
        _active(draft_transfer)

        response = client.get(_url(draft_transfer, file))
        page = client.get(reverse('t:download', args=[draft_transfer.slug]))

        assert response['Content-Type'].startswith('text/plain')
        assert 'html' not in response['Content-Type'].replace('plain', '')
        entries = page.context['previews']
        assert [entry['kind'] for entry in entries] == ['text']
        # No Formatted / Raw toggle is offered for it: the dialog only toggles `markdown`.
        assert entries[0]['kind'] != 'markdown'

    def test_a_file_that_sniffs_as_nothing_has_no_preview(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(draft_transfer, fake_storage, 'photo.png', b'\0' * 64)
        _active(draft_transfer)
        assert client.get(_url(draft_transfer, file)).status_code == 404

    def test_text_is_read_from_the_head_and_capped_at_one_megabyte(
        self, client, draft_transfer, fake_storage
    ):
        content = b'a' * (preview.TEXT_PREVIEW_BYTES + 500)
        file = _add(draft_transfer, fake_storage, 'big.txt', content)
        _active(draft_transfer)

        response = client.get(_url(draft_transfer, file))

        assert len(response.content) == preview.TEXT_PREVIEW_BYTES

    def test_a_character_cut_by_the_cap_does_not_leave_garbage(
        self, client, draft_transfer, fake_storage
    ):
        content = b'a' * (preview.TEXT_PREVIEW_BYTES - 1) + 'é'.encode() + b'tail'
        file = _add(draft_transfer, fake_storage, 'cut.txt', content)
        _active(draft_transfer)
        text = client.get(_url(draft_transfer, file)).content.decode()
        assert '�' not in text

    def test_markdown_answers_json_with_the_raw_text_and_sanitised_html(
        self, client, draft_transfer, fake_storage
    ):
        source = (
            b'# Title\n\nSome **bold** text and <script>alert(1)</script>\n\n![x](http://e/x.png)\n'
        )
        file = _add(draft_transfer, fake_storage, 'README.md', source)
        _active(draft_transfer)

        response = client.get(_url(draft_transfer, file))

        assert response.status_code == 200
        assert response['Content-Type'] == 'application/json'
        assert response['X-Content-Type-Options'] == 'nosniff'
        data = json.loads(response.content)
        assert data['text'] == source.decode()
        assert '<h1>Title</h1>' in data['html']
        assert '<strong>bold</strong>' in data['html']
        # Raw HTML in the file is escaped, never rendered; images are never rendered.
        assert '<script' not in data['html']
        assert '&lt;script&gt;' in data['html']
        assert '<img' not in data['html']
        assert data['html_truncated'] is False

    def test_a_long_markdown_file_is_rendered_up_to_the_renderers_limit(
        self, client, draft_transfer, fake_storage
    ):
        from apps.core.markdown import MAX_MARKDOWN_CHARS

        source = ('line of text\n' * 10_000).encode()
        assert len(source) > MAX_MARKDOWN_CHARS
        file = _add(draft_transfer, fake_storage, 'long.md', source)
        _active(draft_transfer)

        data = json.loads(client.get(_url(draft_transfer, file)).content)

        assert data['html_truncated'] is True
        assert data['html']
        assert data['text'] == source.decode()

    def test_a_markdown_file_named_md_but_binary_is_not_previewed(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(draft_transfer, fake_storage, 'notes.md', b'\x00\x01\x02\x03')
        _active(draft_transfer)
        assert client.get(_url(draft_transfer, file)).status_code == 404

    def test_an_unknown_file_or_one_of_another_transfer_is_404(
        self, client, draft_transfer, fake_storage, funded_user
    ):
        from ..models import Transfer

        file = _add(draft_transfer, fake_storage, 'a.png', PNG)
        _active(draft_transfer)
        other = Transfer.objects.create(owner=funded_user, status=TransferStatus.ACTIVE)

        assert client.get(_url(other, file)).status_code == 404
        missing = reverse(
            't:preview', args=[draft_transfer.slug, '00000000-0000-0000-0000-000000000000']
        )
        assert client.get(missing).status_code == 404

    def test_a_file_still_uploading_is_not_previewable(self, client, draft_transfer, fake_storage):
        file = services.add_file(draft_transfer, 'a.png', 10, storage=fake_storage)
        TransferFile.objects.filter(pk=file.pk).update(preview_type='image/png')
        _active(draft_transfer)
        assert client.get(_url(draft_transfer, file)).status_code == 404

    def test_post_is_not_allowed(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'a.png', PNG)
        _active(draft_transfer)
        assert client.post(_url(draft_transfer, file)).status_code == 405


class TestPreviewGates:
    """The same gates as the download."""

    def test_a_password_blocks_it_until_unlocked(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'a.png', PNG)
        _active(draft_transfer, password_hash=password_service.hash_password('sekret'))

        locked = client.get(_url(draft_transfer, file))
        assert locked.status_code == 403
        assert 'Location' not in locked
        assert fake_storage.get_url_calls == []

        client.post(reverse('t:unlock', args=[draft_transfer.slug]), {'password': 'sekret'})
        assert client.get(_url(draft_transfer, file)).status_code == 302

    @pytest.mark.parametrize(
        'overrides',
        [
            {'status': TransferStatus.DISABLED},
            {'status': TransferStatus.SUSPENDED},
            {'held_for_review_at': timezone.now()},
        ],
    )
    def test_an_unavailable_transfer_is_404(self, client, draft_transfer, fake_storage, overrides):
        file = _add(draft_transfer, fake_storage, 'a.png', PNG)
        _active(draft_transfer)
        for key, value in overrides.items():
            setattr(draft_transfer, key, value)
        draft_transfer.save()
        assert client.get(_url(draft_transfer, file)).status_code == 404

    def test_an_expired_transfer_is_404(self, client, draft_transfer, fake_storage):
        from datetime import timedelta

        file = _add(draft_transfer, fake_storage, 'a.png', PNG)
        _active(draft_transfer, expires_at=timezone.now() - timedelta(minutes=1))
        assert client.get(_url(draft_transfer, file)).status_code == 404

    def test_a_taken_down_transfer_is_404(self, client, draft_transfer, fake_storage, admin_user):
        file = _add(draft_transfer, fake_storage, 'a.png', PNG)
        _active(draft_transfer)
        take_down_transfer(draft_transfer, by=admin_user, reason='abuse')
        assert client.get(_url(draft_transfer, file)).status_code == 404

    def test_rate_limited_per_ip(self, client, draft_transfer, fake_storage, settings):
        file = _add(draft_transfer, fake_storage, 'a.png', PNG)
        _active(draft_transfer)
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_PREVIEW_IP': (1, 60)}

        assert client.get(_url(draft_transfer, file)).status_code == 302
        limited = client.get(_url(draft_transfer, file))
        assert limited.status_code == 429
        assert 'Retry-After' in limited

    def test_the_rule_exists_with_a_sane_default(self, settings):
        limit, window = settings.RATELIMIT_RULES['FT_PREVIEW_IP']
        assert limit >= 60 and window == 60


class TestPreviewOfARequest:
    def test_only_the_owner_before_it_is_done_and_everyone_with_access_after(
        self, client, make_request, funded_user, fake_storage, upload_to_request, anon_uploader
    ):
        from django.test import Client

        transfer = make_request()
        file = request_upload.add_file(
            transfer, anon_uploader, 'p.png', len(PNG), ip='9.9.9.9', storage=fake_storage
        )
        fake_storage.seed_object(file.storage_key, len(PNG), PNG)
        request_upload.complete_file(
            transfer,
            file,
            [{'PartNumber': 1, 'ETag': 'e'}],
            anon_uploader,
            ip='9.9.9.9',
            storage=fake_storage,
        )
        url = _url(transfer, file)

        assert client.get(url).status_code == 403  # open: not the owner
        owner = Client()
        owner.force_login(funded_user)
        assert owner.get(url).status_code == 302  # open: the owner may

        requests_service.mark_done(transfer, by_name='Jane', storage=fake_storage)

        assert client.get(url).status_code == 302
        page = client.get(reverse('t:download', args=[transfer.slug]))
        assert page.status_code == 200
        assert 'Preview p.png' in page.content.decode()
        assert not DownloadEvent.objects.exists()


# -- The page --


class TestDownloadPage:
    def _page(self, client, transfer):
        return client.get(reverse('t:download', args=[transfer.slug])).content.decode()

    def test_many_files_some_previewable(self, client, draft_transfer, fake_storage):
        _add(draft_transfer, fake_storage, 'a.png', PNG)
        _add(draft_transfer, fake_storage, 'b.pdf', PDF)
        _add(draft_transfer, fake_storage, 'c.bin', b'\0' * 40)
        _active(draft_transfer)

        content = self._page(client, draft_transfer)

        assert '2 of 3 files can be previewed before you download them.' in content
        assert 'aria-label="Preview a.png"' in content
        assert 'aria-label="Preview b.pdf"' in content
        assert 'Preview c.bin' not in content
        assert 'no preview' in content
        assert 'id="ft-previews"' in content
        assert 'file_transfer/js/preview.js' in content

    def test_one_previewable_file(self, client, draft_transfer, fake_storage):
        _add(draft_transfer, fake_storage, 'a.png', PNG)
        _active(draft_transfer)
        content = self._page(client, draft_transfer)
        assert 'You can preview this file before you download it.' in content
        assert 'no preview' not in content

    def test_all_files_previewable(self, client, draft_transfer, fake_storage):
        _add(draft_transfer, fake_storage, 'a.png', PNG)
        _add(draft_transfer, fake_storage, 'b.txt', b'hi')
        _active(draft_transfer)
        assert 'All 2 files can be previewed' in self._page(client, draft_transfer)

    def test_one_of_several(self, client, draft_transfer, fake_storage):
        _add(draft_transfer, fake_storage, 'a.png', PNG)
        _add(draft_transfer, fake_storage, 'b.bin', b'\0' * 9)
        _active(draft_transfer)
        assert '1 of 2 files can be previewed before you download it.' in self._page(
            client, draft_transfer
        )

    def test_nothing_previewable_says_nothing(self, client, draft_transfer, fake_storage):
        _add(draft_transfer, fake_storage, 'a.bin', b'\0' * 9)
        _active(draft_transfer)
        content = self._page(client, draft_transfer)
        assert 'previewed' not in content
        assert 'no preview' not in content
        assert 'ft-previews' not in content
        assert 'preview.js' not in content

    def test_nothing_opens_by_itself(self, client, draft_transfer, fake_storage):
        _add(draft_transfer, fake_storage, 'a.png', PNG)
        _active(draft_transfer)
        content = self._page(client, draft_transfer)
        # The dialog is closed until a click: it starts hidden and no file is selected.
        assert 'id="preview-modal"' in content
        assert 'x-show="isOpen" x-cloak style="display: none"' in content

    def test_a_locked_page_has_the_hint_but_no_buttons_and_no_preview_urls(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(draft_transfer, fake_storage, 'a.png', PNG)
        _add(draft_transfer, fake_storage, 'b.bin', b'\0' * 9)
        _active(draft_transfer, password_hash=password_service.hash_password('sekret'))

        content = self._page(client, draft_transfer)

        assert '1 of 2 files can be previewed once you enter the password.' in content
        assert 'Preview a.png' not in content
        assert 'ft-previews' not in content
        assert _url(draft_transfer, file) not in content
        assert 'Locked' in content

    def test_the_footer_does_not_claim_anything_about_downloads(
        self, client, draft_transfer, fake_storage
    ):
        _add(draft_transfer, fake_storage, 'a.png', PNG)
        _active(draft_transfer)
        assert "doesn't count as a download" not in self._page(client, draft_transfer)

    def test_the_markdown_switch_is_in_the_dialog(self, client, draft_transfer, fake_storage):
        _add(draft_transfer, fake_storage, 'a.md', b'# hi')
        _active(draft_transfer)
        content = self._page(client, draft_transfer)
        assert 'aria-pressed' in content
        assert 'Formatted' in content and 'Raw' in content
        assert [
            e['kind']
            for e in client.get(reverse('t:download', args=[draft_transfer.slug])).context[
                'previews'
            ]
        ] == ['markdown']

    def test_a_hostile_file_name_is_escaped(self, client, draft_transfer, fake_storage):
        _add(draft_transfer, fake_storage, '"><script>x</script>.png', PNG)
        _active(draft_transfer)
        content = self._page(client, draft_transfer)
        assert '<script>x</script>' not in content
        assert '\\u003Cscript\\u003E' in content or '&lt;script&gt;' in content


# -- Backfill --


class TestBackfill:
    def test_files_without_a_type_get_one(self, draft_transfer, fake_storage):
        png = _add(draft_transfer, fake_storage, 'a.png', PNG)
        zeros = _add(draft_transfer, fake_storage, 'b.bin', b'\0' * 9)
        TransferFile.objects.update(preview_type='')
        _active(draft_transfer)

        looked, found = preview.backfill_previews(storage=fake_storage)

        assert (looked, found) == (2, 1)
        assert TransferFile.objects.get(pk=png.pk).preview_type == 'image/png'
        assert TransferFile.objects.get(pk=zeros.pk).preview_type == ''

    def test_the_command_runs(self, draft_transfer, fake_storage):
        _add(draft_transfer, fake_storage, 'a.png', PNG)
        TransferFile.objects.update(preview_type='')
        _active(draft_transfer)
        out = io.StringIO()
        call_command('file_transfer_backfill_previews', stdout=out)
        assert '1 can now be previewed' in out.getvalue()
        assert TransferFile.objects.get().preview_type == 'image/png'
