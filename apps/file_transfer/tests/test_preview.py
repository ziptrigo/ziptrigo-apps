"""File previews on the public download page (issue #311): what is previewable (decided from the
file's bytes at upload), the text sample endpoint and its gates, and the page's hint and buttons.
The downsized pages of images and PDFs are `test_renditions.py`; "View full" is `test_preview_full.py`."""

import io
import json
import re
from html.parser import HTMLParser

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
MP4 = b'\0\0\0\x18ftypisom\0\0\2\0isomiso2'  # no preview any more: it has NULs, so it is no text
TEXT = b'hello, a text file\n'
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
            (MP4, 'a.mp4', ''),
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


# -- The text sample endpoint --


class TestPreviewEndpoint:
    def test_an_image_or_pdf_has_no_answer_here_the_file_itself_is_never_served(
        self, client, draft_transfer, fake_storage
    ):
        """A preview is a sample: the whole-file inline redirect of the first version is gone."""
        png = _add(draft_transfer, fake_storage, 'misnamed.txt', PNG)
        pdf = _add(draft_transfer, fake_storage, 'a.pdf', PDF)
        _active(draft_transfer)

        for file in (png, pdf):
            response = client.get(_url(draft_transfer, file))
            assert response.status_code == 404
            assert 'Location' not in response
        assert fake_storage.get_url_calls == []

    def test_a_preview_is_not_a_download(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'a.txt', b'hello')
        _active(draft_transfer, max_downloads=1)

        for _ in range(3):
            assert client.get(_url(draft_transfer, file)).status_code == 200

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

    def test_text_is_a_sample_of_the_first_64_kb(self, client, draft_transfer, fake_storage):
        assert preview.TEXT_PREVIEW_BYTES == 64 * 1024
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

    def test_the_sample_is_cached_per_file(self, client, draft_transfer, fake_storage, monkeypatch):
        text = _add(draft_transfer, fake_storage, 'a.txt', b'hello')
        markdown = _add(draft_transfer, fake_storage, 'a.md', b'# hi')
        _active(draft_transfer)
        reads = []
        real = fake_storage.get_object_range

        def counting(key, start, end):
            reads.append(key)
            return real(key, start, end)

        monkeypatch.setattr(fake_storage, 'get_object_range', counting)

        for file in (text, markdown):
            first = client.get(_url(draft_transfer, file))
            second = client.get(_url(draft_transfer, file))
            assert first.status_code == second.status_code == 200
            assert first.content == second.content
        assert reads == [text.storage_key, markdown.storage_key]

    def test_the_cache_is_not_a_gate(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'a.txt', b'hello')
        _active(draft_transfer, password_hash=password_service.hash_password('sekret'))
        client.post(reverse('t:unlock', args=[draft_transfer.slug]), {'password': 'sekret'})
        assert client.get(_url(draft_transfer, file)).status_code == 200  # cached now

        from django.test import Client

        stranger = Client()  # no unlock in this session
        assert stranger.get(_url(draft_transfer, file)).status_code == 403
        TransferFile.objects.filter(pk=file.pk).update(preview_type='text/plain')
        draft_transfer.status = TransferStatus.DISABLED
        draft_transfer.save()
        assert client.get(_url(draft_transfer, file)).status_code == 404

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

    def test_a_long_markdown_sample_is_rendered_up_to_the_renderers_limit(
        self, client, draft_transfer, fake_storage
    ):
        from apps.core.markdown import MAX_MARKDOWN_CHARS

        # 64 KB of text is more than the renderer's 50,000-character guard.
        source = ('line of text\n' * 10_000).encode()
        assert preview.TEXT_PREVIEW_BYTES > MAX_MARKDOWN_CHARS
        file = _add(draft_transfer, fake_storage, 'long.md', source)
        _active(draft_transfer)

        data = json.loads(client.get(_url(draft_transfer, file)).content)

        assert data['html_truncated'] is True
        assert data['html']
        assert data['text'] == source[: preview.TEXT_PREVIEW_BYTES].decode()

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

        file = _add(draft_transfer, fake_storage, 'a.txt', b'hello')
        _active(draft_transfer)
        other = Transfer.objects.create(owner=funded_user, status=TransferStatus.ACTIVE)

        assert client.get(_url(other, file)).status_code == 404
        missing = reverse(
            't:preview', args=[draft_transfer.slug, '00000000-0000-0000-0000-000000000000']
        )
        assert client.get(missing).status_code == 404

    def test_a_file_still_uploading_is_not_previewable(self, client, draft_transfer, fake_storage):
        file = services.add_file(draft_transfer, 'a.txt', 10, storage=fake_storage)
        TransferFile.objects.filter(pk=file.pk).update(preview_type='text/plain')
        _active(draft_transfer)
        assert client.get(_url(draft_transfer, file)).status_code == 404

    def test_post_is_not_allowed(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'a.txt', b'hello')
        _active(draft_transfer)
        assert client.post(_url(draft_transfer, file)).status_code == 405


class _Attributes(HTMLParser):
    """Every attribute of every tag in some HTML."""

    def __init__(self):
        super().__init__()
        self.attributes: list[tuple[str, str, str | None]] = []
        self.tags: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend((tag, name, value) for name, value in attrs)


HOSTILE_MARKDOWN = """
<div x-init="alert(1)">a</div>

<span @click="alert(1)">b</span>

<p :class="alert(1)" x-on:click="alert(2)" onclick="alert(3)">c</p>

[x](javascript:alert(1))

[y](JaVaScRiPt:alert(1))

[z](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)

<img src=x onerror=alert(1)>

<svg onload=alert(1)><a xlink:href="javascript:alert(1)">s</a></svg>

<a href="javascript:alert(1)" x-data="{}">raw anchor</a>

```a"b' onmouseover=alert(1) x-data=1 @click=alert(2)
code
```

```js" onclick="alert(1)
more code
```

| a | b |
|---|---|
| <span x-init="alert(1)">t</span> | [q](javascript:alert(1)) |

- [ ] <b @click="alert(1)">item</b>
"""


class TestMarkdownIsSafeForXHtml:
    """The dialog puts the rendered Markdown in with Alpine's `x-html`, so nothing Alpine acts on
    (`x-*`, `@*`, `:*`) or the browser runs (`on*`, a `javascript:` link) may survive the render:
    that rests on `apps.core.markdown`'s nh3 allow-list, which this pins."""

    def _attributes(self, html: str):
        parser = _Attributes()
        parser.feed(html)
        return parser.attributes

    def _tags(self, html: str) -> set[str]:
        parser = _Attributes()
        parser.feed(html)
        return set(parser.tags)

    def _assert_safe(self, html: str):
        attributes = self._attributes(html)
        for tag, name, value in attributes:
            assert not name.startswith(('x-', '@', ':', 'on')), (tag, name, value)
            assert name not in ('style', 'srcdoc', 'formaction')
            if value is not None and name in ('href', 'src', 'xlink:href', 'action'):
                assert not value.strip().lower().startswith(('javascript:', 'data:', 'vbscript:'))

    def test_the_renderer_strips_everything_alpine_or_the_browser_would_run(self):
        rendered = preview.render_markdown_preview(HOSTILE_MARKDOWN.encode())
        html = rendered['html']
        assert html
        self._assert_safe(html)
        # The hostile markup is shown as *text* (escaped), not parsed as elements.
        tags = {tag for tag, _, _ in self._attributes(html)} | self._tags(html)
        assert not tags & {'script', 'img', 'svg', 'iframe', 'style', 'object', 'embed'}
        assert '&lt;div x-init=' in html

    def test_through_the_endpoint(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'evil.md', HOSTILE_MARKDOWN.encode())
        _active(draft_transfer)

        data = json.loads(client.get(_url(draft_transfer, file)).content)

        self._assert_safe(data['html'])
        # The raw view is textContent; the JSON still carries the file verbatim.
        assert data['text'] == HOSTILE_MARKDOWN

    def test_the_only_x_html_in_the_dialog_is_the_markdown_one(self):
        from pathlib import Path

        dialog = (
            Path(preview.__file__).parents[1]
            / 'templates/file_transfer/partials/preview_dialog.html'
        )
        source = dialog.read_text()
        assert len(re.findall(r'x-html=', source)) == 1
        assert 'x-html="html"' in source


class TestPreviewGates:
    """The same gates as the download."""

    def test_a_password_blocks_it_until_unlocked(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'a.txt', TEXT)
        _active(draft_transfer, password_hash=password_service.hash_password('sekret'))

        locked = client.get(_url(draft_transfer, file))
        assert locked.status_code == 403
        assert 'Location' not in locked
        assert fake_storage.get_url_calls == []

        client.post(reverse('t:unlock', args=[draft_transfer.slug]), {'password': 'sekret'})
        assert client.get(_url(draft_transfer, file)).status_code == 200

    @pytest.mark.parametrize(
        'overrides',
        [
            {'status': TransferStatus.DISABLED},
            {'status': TransferStatus.SUSPENDED},
            {'held_for_review_at': timezone.now()},
        ],
    )
    def test_an_unavailable_transfer_is_404(self, client, draft_transfer, fake_storage, overrides):
        file = _add(draft_transfer, fake_storage, 'a.txt', TEXT)
        _active(draft_transfer)
        for key, value in overrides.items():
            setattr(draft_transfer, key, value)
        draft_transfer.save()
        assert client.get(_url(draft_transfer, file)).status_code == 404

    def test_an_expired_transfer_is_404(self, client, draft_transfer, fake_storage):
        from datetime import timedelta

        file = _add(draft_transfer, fake_storage, 'a.txt', TEXT)
        _active(draft_transfer, expires_at=timezone.now() - timedelta(minutes=1))
        assert client.get(_url(draft_transfer, file)).status_code == 404

    def test_a_taken_down_transfer_is_404(self, client, draft_transfer, fake_storage, admin_user):
        file = _add(draft_transfer, fake_storage, 'a.txt', TEXT)
        _active(draft_transfer)
        take_down_transfer(draft_transfer, by=admin_user, reason='abuse')
        assert client.get(_url(draft_transfer, file)).status_code == 404

    def test_rate_limited_per_ip(self, client, draft_transfer, fake_storage, settings):
        file = _add(draft_transfer, fake_storage, 'a.txt', TEXT)
        _active(draft_transfer)
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_PREVIEW_IP': (1, 60)}

        assert client.get(_url(draft_transfer, file)).status_code == 200
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
            transfer, anon_uploader, 'p.txt', len(TEXT), ip='9.9.9.9', storage=fake_storage
        )
        fake_storage.seed_object(file.storage_key, len(TEXT), TEXT)
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
        assert owner.get(url).status_code == 200  # open: the owner may

        requests_service.mark_done(transfer, by_name='Jane', storage=fake_storage)

        assert client.get(url).status_code == 200
        page = client.get(reverse('t:download', args=[transfer.slug]))
        assert page.status_code == 200
        assert 'Preview p.txt' in page.content.decode()
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
        assert f'/preview/{file.id}' not in content
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

    def test_the_entries_of_the_dialog(self, client, draft_transfer, fake_storage):
        image = _add(draft_transfer, fake_storage, 'a.png', PNG)
        pdf = _add(draft_transfer, fake_storage, 'b.pdf', PDF)
        small = _add(draft_transfer, fake_storage, 'c.txt', b'hi')
        cut = _add(draft_transfer, fake_storage, 'd.txt', b'x' * (preview.TEXT_PREVIEW_BYTES + 1))
        huge = _add(
            draft_transfer,
            fake_storage,
            'e.md',
            b'#' + b'x' * 5000,
            size=preview.FULL_TEXT_MAX_BYTES + 1,
        )
        _active(draft_transfer)

        entries = {
            e['id']: e
            for e in client.get(reverse('t:download', args=[draft_transfer.slug])).context[
                'previews'
            ]
        }

        def args(file):
            return [draft_transfer.slug, file.id]

        # Image and PDF: the build's status endpoint, and View full (a download).
        assert entries[str(image.id)]['url'] == reverse('t:preview-status', args=args(image))
        assert entries[str(image.id)]['full_label'] == 'View full image'
        assert entries[str(pdf.id)]['url'] == reverse('t:preview-status', args=args(pdf))
        assert entries[str(pdf.id)]['full_label'] == 'View full document'
        assert entries[str(image.id)]['full_url'] == reverse('t:preview-full', args=args(image))
        # Text: the sample itself; View full only for one the sample cut and that is not huge.
        assert entries[str(small.id)]['url'] == reverse('t:preview', args=args(small))
        assert (entries[str(small.id)]['full_url'], entries[str(small.id)]['sample_cut']) == (
            '',
            False,
        )
        assert entries[str(cut.id)]['full_label'] == 'View full document'
        assert entries[str(cut.id)]['sample_cut'] is True
        assert (entries[str(huge.id)]['full_url'], entries[str(huge.id)]['sample_cut']) == (
            '',
            True,
        )

    def test_video_and_audio_have_no_preview(self, client, draft_transfer, fake_storage):
        mp3 = b'ID3\x04\x00\x00\x00\x00\x00\x00' + b'\0' * 30
        _add(draft_transfer, fake_storage, 'a.mp4', MP4)
        _add(draft_transfer, fake_storage, 'b.mp3', mp3)
        _active(draft_transfer)
        response = client.get(reverse('t:download', args=[draft_transfer.slug]))
        assert response.context['previews'] == []
        assert 'previewed' not in response.content.decode()

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
