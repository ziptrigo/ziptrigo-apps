"""The downsized previews of images and PDFs (issue #311): what is built, the lazy claim and its
lease, the budget of the child process, the endpoints' gates, and every way the pages are deleted."""

import io
import json
import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .. import services
from ..models import DownloadEvent, PreviewStatus, Transfer, TransferFile, TransferStatus
from ..services import password as password_service
from ..services import preview, rendition_render, renditions, request_upload
from ..services import requests as requests_service
from ..services import uploads as uploads_service
from ..services.lifecycle import delete_transfer_files, end_transfer
from ..services.takedown import take_down_transfer

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _png(width: int = 40, height: int = 30) -> bytes:
    out = io.BytesIO()
    Image.new('RGB', (width, height), (10, 120, 200)).save(out, format='PNG')
    return out.getvalue()


def _jpeg_with_metadata(width: int = 300, height: int = 200) -> bytes:
    """A landscape JPEG whose EXIF says "rotate me" and which carries a camera name and an ICC
    profile."""
    exif = Image.Exif()
    exif[274] = 6  # orientation: rotate 90 degrees
    exif[271] = 'SecretCameraMaker'
    out = io.BytesIO()
    Image.new('RGB', (width, height), (200, 30, 30)).save(
        out, format='JPEG', exif=exif, icc_profile=b'ICCPROFILE' * 20
    )
    return out.getvalue()


def _pdf(pages: int = 3, size: tuple[int, int] = (612, 792)) -> bytes:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        document.new_page(*size)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _add(transfer, fake_storage, name, content):
    """A finished upload of `content` (added as the draft it was, whatever the transfer is now)."""
    status = transfer.status
    transfer.status = TransferStatus.DRAFT
    try:
        file = services.add_file(transfer, name, len(content), storage=fake_storage)
        fake_storage.seed_object(file.storage_key, len(content), content)
        services.complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'e'}], storage=fake_storage)
    finally:
        transfer.status = status
    file.refresh_from_db()
    return file


def _active(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


def _build(file, storage):
    """Claim and run the build (the queued task never runs in a test's transaction)."""
    assert renditions.ensure_build_started(file)
    fresh = TransferFile.objects.select_related('transfer').get(pk=file.pk)
    renditions.run_build(fresh, storage=storage)
    return TransferFile.objects.get(pk=file.pk)


def _status_url(transfer, file, start=False):
    return reverse('t:preview-status', args=[transfer.slug, file.id]) + (
        '?start=1' if start else ''
    )


def _page_url(transfer, file, page):
    return reverse('t:preview-page', args=[transfer.slug, file.id, page])


@pytest.fixture
def active(draft_transfer):
    return _active(draft_transfer)


# -- What is built --


class TestWhatIsBuilt:
    def test_an_image_is_one_small_webp_with_no_metadata_and_its_orientation_applied(
        self, active, fake_storage
    ):
        file = _add(active, fake_storage, 'photo.jpg', _jpeg_with_metadata(3000, 2000))

        file = _build(file, fake_storage)

        assert (file.preview_status, file.preview_pages, file.preview_total_pages) == (
            PreviewStatus.READY,
            1,
            1,
        )
        key = renditions.preview_key(active.pk, file.pk, 1)
        assert key == f'transfers/{active.pk}/previews/{file.pk}/1.webp'
        data = fake_storage.objects[key]
        assert fake_storage.content_types[key] == 'image/webp'
        image = Image.open(io.BytesIO(data))
        assert image.format == 'WEBP'
        # 3000x2000 rotated by its EXIF orientation is 2000x3000: the long side is 1600.
        assert image.size == (1067, 1600)
        for marker in (b'EXIF', b'ICCP', b'XMP ', b'SecretCameraMaker'):
            assert marker not in data
        # The original is untouched.
        assert fake_storage.objects[file.storage_key].startswith(b'\xff\xd8')

    def test_a_small_image_is_never_enlarged(self, active, fake_storage):
        file = _build(_add(active, fake_storage, 'tiny.png', _png(40, 30)), fake_storage)
        data = fake_storage.objects[renditions.preview_key(active.pk, file.pk, 1)]
        assert Image.open(io.BytesIO(data)).size == (40, 30)

    def test_the_first_frame_of_a_gif(self, active, fake_storage):
        out = io.BytesIO()
        frames = [Image.new('P', (20, 20), i) for i in range(3)]
        frames[0].save(out, format='GIF', save_all=True, append_images=frames[1:])
        file = _build(_add(active, fake_storage, 'a.gif', out.getvalue()), fake_storage)
        assert file.preview_status == PreviewStatus.READY
        assert file.preview_pages == 1

    def test_a_pdf_is_one_webp_per_page_the_longest_side_within_1600(self, active, fake_storage):
        file = _add(active, fake_storage, 'doc.pdf', _pdf(3))

        file = _build(file, fake_storage)

        assert (file.preview_pages, file.preview_total_pages) == (3, 3)
        for number in (1, 2, 3):
            data = fake_storage.objects[renditions.preview_key(active.pk, file.pk, number)]
            width, height = Image.open(io.BytesIO(data)).size
            assert max(width, height) <= rendition_render.MAX_SIDE_PX
        assert renditions.preview_key(active.pk, file.pk, 4) not in fake_storage.objects

    def test_a_big_page_is_scaled_down_to_fit(self, active, fake_storage):
        file = _build(_add(active, fake_storage, 'poster.pdf', _pdf(1, (5000, 3000))), fake_storage)
        data = fake_storage.objects[renditions.preview_key(active.pk, file.pk, 1)]
        assert Image.open(io.BytesIO(data)).size == (1600, 960)

    def test_only_the_first_20_pages_are_rendered(self, active, fake_storage):
        assert renditions.MAX_PREVIEW_PAGES == 20
        file = _build(_add(active, fake_storage, 'long.pdf', _pdf(25)), fake_storage)
        assert (file.preview_pages, file.preview_total_pages) == (20, 25)
        assert renditions.preview_key(active.pk, file.pk, 20) in fake_storage.objects
        assert renditions.preview_key(active.pk, file.pk, 21) not in fake_storage.objects

    def test_not_billed_and_not_part_of_the_size(self, active, fake_storage):
        file = _add(active, fake_storage, 'a.png', _png())
        active.refresh_from_db()
        before = active.size_bytes
        _build(file, fake_storage)
        active.refresh_from_db()
        assert active.size_bytes == before


class TestFilesThatCannotBeReduced:
    def test_an_image_over_the_pixel_cap_is_unavailable_before_it_is_decoded(
        self, active, fake_storage, monkeypatch
    ):
        source = _add(active, fake_storage, 'big.png', _png(40, 30))
        monkeypatch.setattr(rendition_render, 'MAX_IMAGE_PIXELS', 100)
        decoded = []
        real_load = Image.Image.load
        monkeypatch.setattr(Image.Image, 'load', lambda self: decoded.append(1) or real_load(self))
        file = _build(source, fake_storage)
        assert file.preview_status == PreviewStatus.UNAVAILABLE
        assert decoded == []
        assert not [k for k in fake_storage.objects if '/previews/' in k]

    def test_the_cap_is_100_megapixels(self):
        assert rendition_render.MAX_IMAGE_PIXELS == 100_000_000

    def test_a_file_over_the_size_cap_is_unavailable_without_a_worker(
        self, active, fake_storage, monkeypatch, django_capture_on_commit_callbacks
    ):
        file = _add(active, fake_storage, 'a.png', _png())
        monkeypatch.setattr(renditions, 'MAX_SOURCE_BYTES', 10)
        with django_capture_on_commit_callbacks() as callbacks:
            assert renditions.ensure_build_started(file) is False
        assert callbacks == []
        assert TransferFile.objects.get(pk=file.pk).preview_status == PreviewStatus.UNAVAILABLE

    def test_the_size_cap_is_checked_again_when_reading(self, active, fake_storage, monkeypatch):
        file = _add(active, fake_storage, 'a.png', _png())
        assert renditions.ensure_build_started(file)
        monkeypatch.setattr(renditions, 'MAX_SOURCE_BYTES', 10)
        renditions.run_build(TransferFile.objects.select_related('transfer').get(pk=file.pk))
        assert TransferFile.objects.get(pk=file.pk).preview_status == PreviewStatus.UNAVAILABLE

    @pytest.mark.parametrize('name', ['broken.png', 'broken.pdf'])
    def test_a_corrupt_file_is_unavailable(self, active, fake_storage, name):
        header = b'\x89PNG\r\n\x1a\n' if name.endswith('png') else b'%PDF-1.7\n'
        file = _build(_add(active, fake_storage, name, header + b'garbage' * 20), fake_storage)
        assert file.preview_status == PreviewStatus.UNAVAILABLE

    def test_an_unavailable_verdict_is_kept_and_never_built_again(
        self, active, fake_storage, django_capture_on_commit_callbacks
    ):
        file = _build(
            _add(active, fake_storage, 'x.png', b'\x89PNG\r\n\x1a\nrubbish'), fake_storage
        )
        assert file.preview_status == PreviewStatus.UNAVAILABLE
        with django_capture_on_commit_callbacks() as callbacks:
            assert renditions.ensure_build_started(file) is False
        assert callbacks == []

    def test_a_transient_failure_is_failed_and_the_next_click_tries_again(
        self, active, fake_storage, monkeypatch, django_capture_on_commit_callbacks
    ):
        file = _add(active, fake_storage, 'a.png', _png())

        def boom(data, kind):
            raise renditions.RenditionFailed('the child was killed')

        real = renditions.render_pages
        monkeypatch.setattr(renditions, 'render_pages', boom)
        file = _build(file, fake_storage)
        assert file.preview_status == PreviewStatus.FAILED
        assert not [k for k in fake_storage.objects if '/previews/' in k]

        monkeypatch.setattr(renditions, 'render_pages', real)
        # Not at once: a failing file may be retried once per cooldown.
        assert not renditions.ensure_build_started(file)
        TransferFile.objects.filter(pk=file.pk).update(
            preview_started_at=timezone.now() - renditions.RETRY_COOLDOWN - timedelta(seconds=1)
        )
        with django_capture_on_commit_callbacks(execute=True):
            assert renditions.ensure_build_started(file)
        assert TransferFile.objects.get(pk=file.pk).preview_status == PreviewStatus.READY

    def test_a_storage_error_is_failed_not_unavailable(self, active, fake_storage, monkeypatch):
        file = _add(active, fake_storage, 'a.png', _png())

        def boom(key, body, *, content_type=None):
            raise RuntimeError('S3 is down')

        monkeypatch.setattr(fake_storage, 'put_object', boom)
        assert _build(file, fake_storage).preview_status == PreviewStatus.FAILED


# -- The claim --


class TestClaim:
    def test_only_one_caller_wins_and_queues_one_build(
        self, active, fake_storage, django_capture_on_commit_callbacks, monkeypatch
    ):
        file = _add(active, fake_storage, 'a.png', _png())
        queued = []
        monkeypatch.setattr(
            renditions,
            'build_preview',
            SimpleNamespace(enqueue=lambda file_id: queued.append(file_id)),
        )

        with django_capture_on_commit_callbacks(execute=True):
            first = renditions.ensure_build_started(file)
            second = renditions.ensure_build_started(file)

        assert (first, second) == (True, False)
        assert queued == [str(file.pk)]
        refreshed = TransferFile.objects.get(pk=file.pk)
        assert refreshed.preview_status == PreviewStatus.BUILDING
        assert refreshed.preview_started_at is not None

    def test_a_build_that_never_finished_can_be_claimed_again_after_the_lease(
        self, active, fake_storage
    ):
        file = _add(active, fake_storage, 'a.png', _png())
        assert renditions.ensure_build_started(file)
        assert not renditions.ensure_build_started(file)
        TransferFile.objects.filter(pk=file.pk).update(
            preview_started_at=timezone.now() - renditions.BUILD_LEASE - timedelta(seconds=1)
        )
        assert renditions.ensure_build_started(file)

    def test_ready_is_never_claimed_and_failed_only_after_the_cooldown(self, active, fake_storage):
        file = _build(_add(active, fake_storage, 'a.png', _png()), fake_storage)
        assert not renditions.ensure_build_started(file)
        TransferFile.objects.filter(pk=file.pk).update(
            preview_status=PreviewStatus.FAILED, preview_started_at=timezone.now()
        )
        assert not renditions.ensure_build_started(file)  # just failed: wait for the cooldown
        TransferFile.objects.filter(pk=file.pk).update(
            preview_started_at=timezone.now() - renditions.RETRY_COOLDOWN - timedelta(seconds=1)
        )
        assert renditions.ensure_build_started(file)
        # A failed file with no start time recorded (damaged data) is not stuck.
        TransferFile.objects.filter(pk=file.pk).update(
            preview_status=PreviewStatus.FAILED, preview_started_at=None
        )
        assert renditions.ensure_build_started(file)

    def test_the_task_does_nothing_unless_the_file_is_building(
        self, active, fake_storage, monkeypatch
    ):
        file = _add(active, fake_storage, 'a.png', _png())
        calls = []
        monkeypatch.setattr(renditions, 'render_pages', lambda *a: calls.append(a))
        renditions.build_preview.call(str(file.pk))  # status `none`
        renditions.build_preview.call('00000000-0000-0000-0000-000000000000')
        assert calls == []

    def test_a_ready_preview_is_reused_not_rebuilt(self, active, fake_storage, monkeypatch):
        file = _build(_add(active, fake_storage, 'a.png', _png()), fake_storage)
        calls = []
        monkeypatch.setattr(renditions, 'render_pages', lambda *a: calls.append(a))
        assert not renditions.ensure_build_started(file)
        renditions.build_preview.call(str(file.pk))
        assert calls == []


# -- Races at the end of a build --


class TestEndOfBuild:
    def _race(self, monkeypatch, action):
        """Run `action` while the build is rendering (after the file was read, before it is stored)."""
        real = renditions.render_pages

        def rendering(data, kind):
            result = real(data, kind)
            action()
            return result

        monkeypatch.setattr(renditions, 'render_pages', rendering)

    def test_a_transfer_that_ended_meanwhile_leaves_no_pages(
        self, active, fake_storage, monkeypatch
    ):
        file = _add(active, fake_storage, 'a.png', _png())
        self._race(
            monkeypatch, lambda: end_transfer(active, TransferStatus.DELETED, storage=fake_storage)
        )

        file = _build(file, fake_storage)

        assert not [k for k in fake_storage.objects if '/previews/' in k]
        assert file.preview_status != PreviewStatus.READY

    def test_a_transfer_that_ends_after_the_pages_were_stored_cleans_up_after_itself(
        self, active, fake_storage, monkeypatch
    ):
        """The prefix sweep ran before the build wrote anything, so only the build can clean up."""
        file = _add(active, fake_storage, 'a.png', _png())
        real_put = fake_storage.put_object

        def put_then_end(key, body, *, content_type=None):
            real_put(key, body, content_type=content_type)
            Transfer.objects.filter(pk=active.pk).update(files_deleted_at=timezone.now())

        monkeypatch.setattr(fake_storage, 'put_object', put_then_end)

        file = _build(file, fake_storage)

        assert not [k for k in fake_storage.objects if '/previews/' in k]
        assert file.preview_status == PreviewStatus.NONE

    def test_a_file_removed_meanwhile_leaves_no_pages(self, active, fake_storage, monkeypatch):
        file = _add(active, fake_storage, 'a.png', _png())
        self._race(monkeypatch, lambda: TransferFile.objects.filter(pk=file.pk).delete())
        assert renditions.ensure_build_started(file)
        renditions.run_build(
            TransferFile.objects.select_related('transfer').get(pk=file.pk), storage=fake_storage
        )
        assert not [k for k in fake_storage.objects if '/previews/' in k]

    def test_a_purge_that_reset_it_meanwhile_leaves_no_pages(
        self, active, fake_storage, monkeypatch
    ):
        file = _add(active, fake_storage, 'a.png', _png())
        self._race(
            monkeypatch,
            lambda: TransferFile.objects.filter(pk=file.pk).update(
                preview_status=PreviewStatus.NONE
            ),
        )
        file = _build(file, fake_storage)
        assert file.preview_status == PreviewStatus.NONE
        assert not [k for k in fake_storage.objects if '/previews/' in k]

    def test_a_sibling_build_that_finished_first_keeps_its_pages(
        self, active, fake_storage, monkeypatch
    ):
        file = _add(active, fake_storage, 'a.png', _png())
        self._race(
            monkeypatch,
            lambda: TransferFile.objects.filter(pk=file.pk).update(
                preview_status=PreviewStatus.READY, preview_pages=1, preview_total_pages=1
            ),
        )
        _build(file, fake_storage)
        assert renditions.preview_key(active.pk, file.pk, 1) in fake_storage.objects

    def test_a_transfer_ended_while_it_was_queued_builds_nothing(self, active, fake_storage):
        file = _add(active, fake_storage, 'a.png', _png())
        assert renditions.ensure_build_started(file)
        Transfer.objects.filter(pk=active.pk).update(status=TransferStatus.EXPIRED)
        renditions.run_build(
            TransferFile.objects.select_related('transfer').get(pk=file.pk), storage=fake_storage
        )
        assert not [k for k in fake_storage.objects if '/previews/' in k]
        assert TransferFile.objects.get(pk=file.pk).preview_status == PreviewStatus.NONE


# -- The child process --


class TestChildProcess:
    def _render(self, **overrides):
        options = {'memory_mb': 1024, 'cpu_seconds': 25, 'timeout': 60, 'child_user': ''}
        return options | overrides

    def test_the_real_child_reduces_an_image_and_a_pdf(self):
        pages, total = renditions.render_in_child(_png(50, 40), 'image', **self._render())
        assert total == 1
        assert Image.open(io.BytesIO(pages[0])).format == 'WEBP'
        pages, total = renditions.render_in_child(_pdf(2), 'pdf', **self._render())
        assert (len(pages), total) == (2, 2)

    def test_the_real_child_reports_a_corrupt_file_as_unavailable(self):
        with pytest.raises(renditions.RenditionUnavailable):
            renditions.render_in_child(b'%PDF-1.7 rubbish', 'pdf', **self._render())

    def test_the_sandbox_is_what_render_pages_uses_when_it_is_on(self, settings, monkeypatch):
        settings.FILE_TRANSFER_PREVIEW_SANDBOX = True
        seen = {}

        def fake_child(data, kind, **kwargs):
            seen.update(kwargs, kind=kind)
            return [b'x'], 1

        monkeypatch.setattr(renditions, 'render_in_child', fake_child)
        assert renditions.render_pages(b'data', 'image') == ([b'x'], 1)
        assert seen['memory_mb'] == settings.FILE_TRANSFER_PREVIEW_MEMORY_MB
        assert seen['cpu_seconds'] == settings.FILE_TRANSFER_PREVIEW_CPU_SECONDS
        assert seen['timeout'] == settings.FILE_TRANSFER_PREVIEW_TIMEOUT_SECONDS

    def test_in_process_under_pytest(self, settings):
        assert settings.FILE_TRANSFER_PREVIEW_SANDBOX is False

    def _run(self, code: str, **options):
        return renditions.render_in_child(
            b'ignored', 'image', **self._render(**options), command=[sys.executable, '-c', code]
        )

    @pytest.mark.parametrize(
        'code',
        [
            'import os, signal; os.kill(os.getpid(), signal.SIGSEGV)',
            'import os, signal; os.kill(os.getpid(), signal.SIGABRT)',
            "import sys; sys.stdout.write('#started\\n'); sys.exit(127)",
            'import sys; sys.stdout.write(\'#started\\n{"error": "too_complex", "message": "big"}\')',
            'import sys; sys.stdout.write(\'{"error": "unreadable", "message": "bad"}\')',
        ],
    )
    def test_what_says_the_file_is_over_budget_or_unreadable_is_unavailable(self, code):
        with pytest.raises(renditions.RenditionUnavailable):
            self._run(code)

    @pytest.mark.parametrize(
        'code',
        [
            'import sys; sys.exit(3)',
            'import os, signal; os.kill(os.getpid(), signal.SIGKILL)',  # the OOM killer
            'import os, signal; os.kill(os.getpid(), signal.SIGTERM)',
            'import sys; sys.exit(127)',  # no marker: a broken install
            "import sys; sys.stdout.write('not json')",
            'import sys; sys.stdout.write(\'{"error": "failed", "message": "x"}\')',
            'import sys; sys.stdout.write(\'{"ok": {"pages": ["!!"], "total_pages": 1}}\')',
            'import sys; sys.stdout.write(\'{"ok": {"pages": [], "total_pages": 1}}\')',
            'import sys; sys.stdout.write(\'{"ok": {"pages": "eA==", "total_pages": 1}}\')',
        ],
    )
    def test_what_says_nothing_about_the_file_is_a_transient_failure(self, code):
        with pytest.raises(renditions.RenditionFailed):
            self._run(code)

    def test_a_child_over_the_wall_clock_is_killed_and_is_a_transient_failure(self):
        with pytest.raises(renditions.RenditionFailed):
            self._run('import time; time.sleep(30)', timeout=0.5)

    def test_a_child_that_cannot_start_is_a_transient_failure(self):
        with pytest.raises(renditions.RenditionFailed):
            renditions.render_in_child(
                b'x', 'image', **self._render(), command=['/no/such/interpreter']
            )

    def test_the_reply_may_carry_the_pages(self):
        reply = json.dumps({'ok': {'pages': ['eA=='], 'total_pages': 7}})
        code = f'import sys; sys.stdout.write("#started\\n" + {reply!r})'
        assert self._run(code) == ([b'x'], 7)

    def test_the_child_gets_no_secrets_in_its_environment(self, monkeypatch):
        monkeypatch.setenv('SECRET_KEY', 'super-secret')
        monkeypatch.setenv('DATABASE_URL', 'postgres://x')
        code = (
            'import os, sys, json; '
            'sys.stdout.write("#started\\n" + json.dumps({"ok": {"pages": ["eA=="], '
            '"total_pages": len([k for k in os.environ if k in ("SECRET_KEY", "DATABASE_URL")])+1}}))'
        )
        assert self._run(code) == ([b'x'], 1)


class TestInProcessMapping:
    def test_a_memory_error_is_a_verdict(self, monkeypatch):
        def boom(kind, data):
            raise MemoryError

        monkeypatch.setattr(rendition_render, 'render', boom)
        with pytest.raises(renditions.RenditionUnavailable):
            renditions.render_pages(b'x', 'image')

    def test_a_missing_library_is_not_the_files_fault(self, monkeypatch):
        def boom(kind, data):
            raise ImportError('no pdfium')

        monkeypatch.setattr(rendition_render, 'render', boom)
        with pytest.raises(renditions.RenditionFailed):
            renditions.render_pages(b'x', 'pdf')


# -- The endpoints --


class TestStatusEndpoint:
    def test_start_builds_then_polls_see_the_pages(
        self, client, active, fake_storage, django_capture_on_commit_callbacks
    ):
        file = _add(active, fake_storage, 'doc.pdf', _pdf(3))

        with django_capture_on_commit_callbacks(execute=True):
            first = client.get(_status_url(active, file, start=True))
        # The queue is inline in tests, so by the time the response is read it is built; a
        # real worker answers `preparing` first.
        assert first.status_code == 200
        data = client.get(_status_url(active, file)).json()
        assert data['status'] == 'ready'
        assert (data['pages'], data['total_pages']) == (3, 3)
        assert data['page_urls'] == [_page_url(active, file, n) for n in (1, 2, 3)]
        assert first['Cache-Control'] == 'no-store, private'
        assert not DownloadEvent.objects.exists()

    def test_the_first_answer_while_building_is_preparing(self, client, active, fake_storage):
        file = _add(active, fake_storage, 'a.png', _png())

        response = client.get(_status_url(active, file, start=True))  # the task is not run here

        assert response.json() == {
            'status': 'preparing',
            'pages': 0,
            'total_pages': 0,
            'page_urls': [],
        }

    def test_polling_never_starts_a_build_so_a_failure_is_reported_not_retried(
        self, client, active, fake_storage
    ):
        file = _add(active, fake_storage, 'a.png', _png())
        assert client.get(_status_url(active, file)).json()['status'] == 'none'
        assert TransferFile.objects.get(pk=file.pk).preview_status == PreviewStatus.NONE
        TransferFile.objects.filter(pk=file.pk).update(preview_status=PreviewStatus.FAILED)
        assert client.get(_status_url(active, file)).json()['status'] == 'failed'
        assert TransferFile.objects.get(pk=file.pk).preview_status == PreviewStatus.FAILED
        # Try again is `start=1`.
        assert client.get(_status_url(active, file, start=True)).json()['status'] == 'preparing'

    def test_unavailable_is_reported(self, client, active, fake_storage):
        file = _build(_add(active, fake_storage, 'x.png', b'\x89PNG\r\n\x1a\njunk'), fake_storage)
        assert client.get(_status_url(active, file, start=True)).json()['status'] == 'unavailable'

    def test_a_text_file_has_no_status(self, client, active, fake_storage):
        file = _add(active, fake_storage, 'a.txt', b'hello')
        assert client.get(_status_url(active, file, start=True)).status_code == 404

    def test_a_file_that_cannot_be_previewed_is_404(self, client, active, fake_storage):
        file = _add(active, fake_storage, 'a.bin', b'\0' * 20)
        assert client.get(_status_url(active, file, start=True)).status_code == 404

    def test_polling_marks_the_pages_used_at_most_once_an_hour(self, client, active, fake_storage):
        file = _build(_add(active, fake_storage, 'a.png', _png()), fake_storage)
        old = timezone.now() - timedelta(days=3)
        TransferFile.objects.filter(pk=file.pk).update(preview_last_used_at=old)
        client.get(_status_url(active, file))
        used = TransferFile.objects.get(pk=file.pk).preview_last_used_at
        assert used > old
        client.get(_status_url(active, file))
        assert TransferFile.objects.get(pk=file.pk).preview_last_used_at == used


class TestPageEndpoint:
    def test_a_page_redirects_to_a_presigned_inline_webp_of_the_page_never_the_file(
        self, client, active, fake_storage
    ):
        file = _build(_add(active, fake_storage, 'doc.pdf', _pdf(2)), fake_storage)

        response = client.get(_page_url(active, file, 2))

        assert response.status_code == 302
        key = renditions.preview_key(active.pk, file.pk, 2)
        assert fake_storage.get_url_calls[-1] == (key, 'inline', 'image/webp')
        assert file.storage_key not in response['Location']
        assert response['Cache-Control'] == 'no-store, private'
        assert response['X-Content-Type-Options'] == 'nosniff'
        assert not DownloadEvent.objects.exists()

    @pytest.mark.parametrize('page', [0, 3, 99])
    def test_a_page_that_does_not_exist_is_404(self, client, active, fake_storage, page):
        file = _build(_add(active, fake_storage, 'doc.pdf', _pdf(2)), fake_storage)
        assert client.get(_page_url(active, file, page)).status_code == 404

    def test_nothing_is_served_before_the_build_is_ready(self, client, active, fake_storage):
        file = _add(active, fake_storage, 'a.png', _png())
        assert client.get(_page_url(active, file, 1)).status_code == 404
        renditions.ensure_build_started(file)
        assert client.get(_page_url(active, file, 1)).status_code == 404

    def test_a_text_file_has_no_pages(self, client, active, fake_storage):
        file = _add(active, fake_storage, 'a.txt', b'hello')
        assert client.get(_page_url(active, file, 1)).status_code == 404

    def test_post_is_not_allowed(self, client, active, fake_storage):
        file = _build(_add(active, fake_storage, 'a.png', _png()), fake_storage)
        assert client.post(_page_url(active, file, 1)).status_code == 405


class TestGates:
    """Every endpoint of a downsized preview has exactly the download's gates."""

    def _endpoints(self, transfer, file):
        return [_status_url(transfer, file, start=True), _page_url(transfer, file, 1)]

    def test_a_password_blocks_them_until_unlocked(self, client, draft_transfer, fake_storage):
        file = _build(_add(draft_transfer, fake_storage, 'a.png', _png()), fake_storage)
        _active(draft_transfer, password_hash=password_service.hash_password('sekret'))

        for url in self._endpoints(draft_transfer, file):
            response = client.get(url)
            assert response.status_code == 403
            assert 'Location' not in response
        assert fake_storage.get_url_calls == []

        client.post(reverse('t:unlock', args=[draft_transfer.slug]), {'password': 'sekret'})
        assert client.get(self._endpoints(draft_transfer, file)[1]).status_code == 302

    @pytest.mark.parametrize(
        'overrides',
        [
            {'status': TransferStatus.DISABLED},
            {'status': TransferStatus.SUSPENDED},
            {'status': TransferStatus.EXPIRED},
            {'held_for_review_at': timezone.now()},
            {'expires_at': timezone.now() - timedelta(minutes=1)},
        ],
    )
    def test_an_unavailable_transfer_is_404(self, client, draft_transfer, fake_storage, overrides):
        file = _build(_add(draft_transfer, fake_storage, 'a.png', _png()), fake_storage)
        _active(draft_transfer, **overrides)
        for url in self._endpoints(draft_transfer, file):
            assert client.get(url).status_code == 404

    def test_a_taken_down_transfer_is_404_and_its_pages_are_gone(
        self, client, draft_transfer, fake_storage, admin_user
    ):
        file = _build(_add(draft_transfer, fake_storage, 'a.png', _png()), fake_storage)
        _active(draft_transfer)
        take_down_transfer(draft_transfer, by=admin_user, reason='abuse')
        for url in self._endpoints(draft_transfer, file):
            assert client.get(url).status_code == 404
        assert not [k for k in fake_storage.objects if '/previews/' in k]

    def test_a_max_downloads_transfer_that_is_full_is_404(
        self, client, draft_transfer, fake_storage
    ):
        file = _build(_add(draft_transfer, fake_storage, 'a.png', _png()), fake_storage)
        _active(draft_transfer, max_downloads=1)
        client.get(reverse('t:download-file', args=[draft_transfer.slug, file.id]))
        for url in self._endpoints(draft_transfer, file):
            assert client.get(url).status_code == 404

    def test_rate_limited_per_ip(self, client, draft_transfer, fake_storage, settings):
        file = _build(_add(draft_transfer, fake_storage, 'a.png', _png()), fake_storage)
        _active(draft_transfer)
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_PREVIEW_IP': (1, 60)}

        assert client.get(_page_url(draft_transfer, file, 1)).status_code == 302
        limited = client.get(_page_url(draft_transfer, file, 1))
        assert limited.status_code == 429
        assert 'Retry-After' in limited

    def test_a_file_request_only_the_owner_until_it_is_done(
        self, client, make_request, funded_user, fake_storage, anon_uploader
    ):
        from django.test import Client

        transfer = make_request()
        content = _png()
        file = request_upload.add_file(
            transfer, anon_uploader, 'p.png', len(content), ip='9.9.9.9', storage=fake_storage
        )
        fake_storage.seed_object(file.storage_key, len(content), content)
        request_upload.complete_file(
            transfer,
            file,
            [{'PartNumber': 1, 'ETag': 'e'}],
            anon_uploader,
            ip='9.9.9.9',
            storage=fake_storage,
        )
        url = _status_url(transfer, file, start=True)

        assert client.get(url).status_code == 403
        owner = Client()
        owner.force_login(funded_user)
        assert owner.get(url).status_code == 200

        requests_service.mark_done(transfer, by_name='Jane', storage=fake_storage)
        assert client.get(url).status_code == 200


# -- Deleting --


def _previews(fake_storage):
    return sorted(k for k in fake_storage.objects if '/previews/' in k)


class TestDeletion:
    def test_ending_a_transfer_deletes_the_pages_with_the_rest_and_resets_the_state(
        self, active, fake_storage
    ):
        file = _build(_add(active, fake_storage, 'doc.pdf', _pdf(2)), fake_storage)
        assert len(_previews(fake_storage)) == 2

        end_transfer(active, TransferStatus.DELETED, storage=fake_storage)

        assert _previews(fake_storage) == []
        file.refresh_from_db()
        assert (file.preview_status, file.preview_pages) == (PreviewStatus.NONE, 0)

    @pytest.mark.parametrize('how', ['expire', 'delete_now_status', 'takedown'])
    def test_every_way_a_transfer_ends(self, active, fake_storage, admin_user, how):
        _build(_add(active, fake_storage, 'a.png', _png()), fake_storage)
        if how == 'takedown':
            take_down_transfer(active, by=admin_user, reason='abuse')
        else:
            status = TransferStatus.EXPIRED if how == 'expire' else TransferStatus.DELETED
            end_transfer(active, status, storage=fake_storage)
        assert _previews(fake_storage) == []

    def test_the_deferred_deletion_of_a_transfer_ended_by_its_last_download_takes_them_too(
        self, active, fake_storage
    ):
        from ..services.lifecycle import finish_deferred_deletion

        _build(_add(active, fake_storage, 'a.png', _png()), fake_storage)
        end_transfer(active, TransferStatus.EXPIRED, delete_files=False, storage=fake_storage)
        assert len(_previews(fake_storage)) == 1  # until the grace window has passed
        finish_deferred_deletion(active, notify=False, storage=fake_storage)
        assert _previews(fake_storage) == []

    def test_removing_a_draft_file_deletes_its_pages_only(self, active, fake_storage):
        kept = _build(_add(active, fake_storage, 'keep.png', _png()), fake_storage)
        gone = _build(_add(active, fake_storage, 'gone.png', _png(20, 20)), fake_storage)

        uploads_service.remove_file(gone, storage=fake_storage)

        assert _previews(fake_storage) == [renditions.preview_key(active.pk, kept.pk, 1)]

    def test_removing_a_request_file_deletes_its_pages(
        self, make_request, fake_storage, anon_uploader, funded_user
    ):
        transfer = make_request()
        content = _png()
        file = request_upload.add_file(
            transfer, anon_uploader, 'p.png', len(content), ip='9.9.9.9', storage=fake_storage
        )
        fake_storage.seed_object(file.storage_key, len(content), content)
        request_upload.complete_file(
            transfer,
            file,
            [{'PartNumber': 1, 'ETag': 'e'}],
            anon_uploader,
            ip='9.9.9.9',
            storage=fake_storage,
        )
        file = _build(TransferFile.objects.get(pk=file.pk), fake_storage)
        assert len(_previews(fake_storage)) == 1

        request_upload.remove_file_as_owner(transfer, file, storage=fake_storage)

        assert _previews(fake_storage) == []
        assert not TransferFile.objects.filter(pk=file.pk).exists()

    def test_an_uploader_removing_their_own_file_deletes_its_pages(
        self, make_request, fake_storage, anon_uploader
    ):
        transfer = make_request()
        content = _png()
        file = request_upload.add_file(
            transfer, anon_uploader, 'p.png', len(content), ip='9.9.9.9', storage=fake_storage
        )
        fake_storage.seed_object(file.storage_key, len(content), content)
        request_upload.complete_file(
            transfer,
            file,
            [{'PartNumber': 1, 'ETag': 'e'}],
            anon_uploader,
            ip='9.9.9.9',
            storage=fake_storage,
        )
        file = _build(TransferFile.objects.get(pk=file.pk), fake_storage)

        request_upload._remove(transfer, file, fake_storage, uploader=True)

        assert _previews(fake_storage) == []

    def test_aborting_a_draft_sweeps_them(self, draft_transfer, fake_storage):
        _build(_add(draft_transfer, fake_storage, 'a.png', _png()), fake_storage)
        uploads_service.abort_draft(draft_transfer, storage=fake_storage)
        assert _previews(fake_storage) == []

    def test_delete_transfer_files_is_idempotent(self, active, fake_storage):
        _build(_add(active, fake_storage, 'a.png', _png()), fake_storage)
        delete_transfer_files(active, storage=fake_storage)
        delete_transfer_files(active, storage=fake_storage)
        assert _previews(fake_storage) == []


class TestPurgeJob:
    def _ready(self, transfer, fake_storage, name, *, used_days_ago):
        file = _build(_add(transfer, fake_storage, name, _png()), fake_storage)
        TransferFile.objects.filter(pk=file.pk).update(
            preview_last_used_at=timezone.now() - timedelta(days=used_days_ago)
        )
        return file

    def test_pages_unused_for_the_cache_days_are_deleted_and_reset(self, active, fake_storage):
        assert renditions.PREVIEW_CACHE_DAYS == 14
        stale = self._ready(active, fake_storage, 'old.png', used_days_ago=15)
        fresh = self._ready(active, fake_storage, 'new.png', used_days_ago=13)

        assert renditions.purge_unused(storage=fake_storage) == 1

        assert _previews(fake_storage) == [renditions.preview_key(active.pk, fresh.pk, 1)]
        stale.refresh_from_db()
        assert (stale.preview_status, stale.preview_pages) == (PreviewStatus.NONE, 0)
        assert TransferFile.objects.get(pk=fresh.pk).preview_status == PreviewStatus.READY
        # The original file is untouched.
        assert stale.storage_key in fake_storage.objects

    def test_the_next_click_builds_them_again(self, active, fake_storage):
        stale = self._ready(active, fake_storage, 'old.png', used_days_ago=30)
        renditions.purge_unused(storage=fake_storage)
        assert renditions.ensure_build_started(TransferFile.objects.get(pk=stale.pk))
        rebuilt = _build_claimed(stale, fake_storage)
        assert rebuilt.preview_status == PreviewStatus.READY
        assert len(_previews(fake_storage)) == 1

    def test_a_use_between_the_read_and_the_claim_wins(self, active, fake_storage, monkeypatch):
        """The state is claimed with the same stale condition the files were found by."""
        file = self._ready(active, fake_storage, 'old.png', used_days_ago=30)
        real = renditions.TransferFile.objects.filter

        calls = []

        def racing(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:  # the first call lists the stale files, the second claims one
                real(pk=file.pk).update(preview_last_used_at=timezone.now())
            return real(*args, **kwargs)

        # The list is read first, then the claim; a click lands in between.
        with monkeypatch.context() as patch:
            patch.setattr(renditions.TransferFile.objects, 'filter', racing)
            renditions.purge_unused(storage=fake_storage)

        assert len(calls) >= 2
        assert TransferFile.objects.get(pk=file.pk).preview_status == PreviewStatus.READY
        assert len(_previews(fake_storage)) == 1

    def test_unavailable_building_and_failed_are_left_alone(self, active, fake_storage):
        old = timezone.now() - timedelta(days=90)
        for status in (PreviewStatus.UNAVAILABLE, PreviewStatus.BUILDING, PreviewStatus.FAILED):
            file = _add(active, fake_storage, f'{status}.png', _png())
            TransferFile.objects.filter(pk=file.pk).update(
                preview_status=status, preview_last_used_at=old, preview_started_at=old
            )
        assert renditions.purge_unused(storage=fake_storage) == 0

    def test_one_failing_file_does_not_stop_the_rest(self, active, fake_storage, monkeypatch):
        first = self._ready(active, fake_storage, 'a.png', used_days_ago=20)
        second = self._ready(active, fake_storage, 'b.png', used_days_ago=20)
        real = fake_storage.delete_prefix

        def flaky(prefix):
            if str(first.pk) in prefix:
                raise RuntimeError('S3 is down')
            return real(prefix)

        monkeypatch.setattr(fake_storage, 'delete_prefix', flaky)
        assert renditions.purge_unused(storage=fake_storage) == 1
        assert renditions.preview_key(active.pk, second.pk, 1) not in fake_storage.objects

    def test_the_job_is_registered_with_a_description(self):
        from apps.core.scheduler import registry

        spec = registry.get_job('file_transfer.purge_previews')
        assert spec is not None
        assert spec.description
        assert spec.interval == timedelta(hours=24)

    def test_the_job_runs_the_purge(self, active, fake_storage):
        from .. import jobs

        self._ready(active, fake_storage, 'old.png', used_days_ago=40)
        jobs.purge_previews()
        assert _previews(fake_storage) == []


def _build_claimed(file, storage):
    """Run the build of a file whose claim a test already made."""
    renditions.run_build(
        TransferFile.objects.select_related('transfer').get(pk=file.pk), storage=storage
    )
    return TransferFile.objects.get(pk=file.pk)


class TestServicesLayerIsExported:
    def test_the_session_guard_helpers_are_exported(self):
        for name in (
            'mark_counted',
            'is_counted',
            'consume_counted',
            'is_replayable',
            'download_url',
        ):
            assert hasattr(services, name)


class TestPreviewTypeUsesRenditions:
    def test_images_and_pdfs_have_renditions_text_does_not(self, active, fake_storage):
        png = _add(active, fake_storage, 'a.png', _png())
        pdf = _add(active, fake_storage, 'a.pdf', _pdf(1))
        text = _add(active, fake_storage, 'a.txt', b'hello')
        assert renditions.has_renditions(png)
        assert renditions.has_renditions(pdf)
        assert not renditions.has_renditions(text)
        assert preview.kind_of(png.preview_type) == preview.PreviewKind.IMAGE
