import io
import logging
import os
import subprocess
import sys
import time
import tracemalloc

import pytest
from PIL import Image, ImageOps

from ..services import documents
from . import samples

pytestmark = [pytest.mark.unit]


class TestSniff:
    @pytest.mark.parametrize(
        ('data', 'expected'),
        [
            (samples.text_pdf_bytes(), 'application/pdf'),
            (samples.jpeg_bytes(), 'image/jpeg'),
            (samples.png_bytes(), 'image/png'),
            (samples.webp_bytes(), 'image/webp'),
            (samples.heic_bytes(), 'image/heic'),
        ],
    )
    def test_attachment_types(self, data, expected):
        assert documents.sniff_content_type(data) == expected

    def test_heif_brands_count_as_heic(self):
        ftyp = b'\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00mif1heic'

        assert documents.sniff_content_type(ftyp) == 'image/heic'

    def test_avif_is_not_accepted(self):
        ftyp = b'\x00\x00\x00\x18ftypavif\x00\x00\x00\x00avifmif1'

        assert documents.sniff_content_type(ftyp) is None

    @pytest.mark.parametrize(
        'data',
        [b'GIF89a....', b'MZ\x90\x00', b'PK\x03\x04zip', b'plain text', b'', b'\x00\x01\x02'],
    )
    def test_other_files_are_not_attachments(self, data):
        assert documents.sniff_content_type(data) is None

    def test_a_pdf_with_leading_whitespace(self):
        assert documents.sniff_content_type(b'\n  %PDF-1.7\n...') == 'application/pdf'

    def test_the_name_or_claimed_type_play_no_part(self):
        # Nothing but the bytes is looked at: an executable is not a PDF.
        assert documents.sniff_content_type(b'MZ' + b'%PDF-' * 4) is None

    def test_email_bodies_may_be_html_or_text(self):
        assert documents.sniff_content_type(samples.HTML_BODY, allow_text=True) == 'text/html'
        assert documents.sniff_content_type(b'Total: 42.10', allow_text=True) == 'text/plain'

    def test_text_is_not_an_attachment_type_unless_asked(self):
        assert documents.sniff_content_type(samples.HTML_BODY) is None
        assert documents.sniff_content_type(b'\x00\xff\xfe binary', allow_text=True) is None


class TestImageDimensions:
    def test_reads_the_size_from_the_header(self):
        assert documents.image_dimensions(samples.png_bytes((40, 70))) == (40, 70)
        assert documents.image_dimensions(samples.jpeg_bytes((120, 30))) == (120, 30)
        assert documents.image_dimensions(samples.heic_bytes((64, 48))) == (64, 48)

    def test_unreadable_data_is_none(self):
        assert documents.image_dimensions(b'not an image') is None
        assert documents.image_dimensions(samples.png_bytes()[:30]) is None


class TestImages:
    def test_a_jpeg_becomes_one_jpeg_with_no_preview(self):
        prepared = documents.prepare_input(samples.jpeg_bytes(), documents.JPEG)

        assert len(prepared.images) == 1
        assert prepared.text == ''
        assert prepared.preview_jpeg is None
        assert documents.sniff_content_type(prepared.images[0]) == 'image/jpeg'

    @pytest.mark.parametrize(
        ('data', 'content_type'),
        [(samples.png_bytes(), documents.PNG), (samples.webp_bytes(), documents.WEBP)],
    )
    def test_png_and_webp_are_converted_to_jpeg(self, data, content_type):
        prepared = documents.prepare_input(data, content_type)

        assert documents.sniff_content_type(prepared.images[0]) == 'image/jpeg'

    def test_heic_is_converted_and_the_jpeg_is_also_the_preview(self):
        prepared = documents.prepare_input(samples.heic_bytes(), documents.HEIC)

        assert documents.sniff_content_type(prepared.images[0]) == 'image/jpeg'
        assert prepared.preview_jpeg == prepared.images[0]

    def test_a_large_image_is_downscaled(self):
        big = samples.jpeg_bytes((5000, 3000))

        prepared = documents.prepare_input(big, documents.JPEG)

        width, height = Image.open(io.BytesIO(prepared.images[0])).size
        assert max(width, height) <= documents.MAX_IMAGE_SIDE_PX
        assert width > height  # the aspect ratio is kept

    def test_a_large_image_is_shrunk_before_it_is_rotated_or_converted(self, monkeypatch):
        """The rotation and the RGBA flattening each copy the bitmap, so they must see the small
        one, not the full-size image (a hostile 7000x7000 PNG used to need ~800 MB)."""
        seen = []
        exif_transpose, convert = ImageOps.exif_transpose, Image.Image.convert
        thumbnail = Image.Image.thumbnail
        shrunk = []

        def spy_thumbnail(image, *args, **kwargs):
            thumbnail(image, *args, **kwargs)
            shrunk.append(True)

        def spy_transpose(image, *args, **kwargs):
            seen.append(('exif_transpose', image.size))
            return exif_transpose(image, *args, **kwargs)

        def spy_convert(image, *args, **kwargs):
            if shrunk:  # (the resampling itself converts the full-size image internally)
                seen.append(('convert', image.size))
            return convert(image, *args, **kwargs)

        monkeypatch.setattr(Image.Image, 'thumbnail', spy_thumbnail)
        monkeypatch.setattr(ImageOps, 'exif_transpose', spy_transpose)
        monkeypatch.setattr(Image.Image, 'convert', spy_convert)
        out = io.BytesIO()
        Image.new('RGBA', (4000, 3000), (255, 255, 255, 128)).save(out, format='PNG')

        prepared = documents.prepare_input(out.getvalue(), documents.PNG)

        assert max(Image.open(io.BytesIO(prepared.images[0])).size) == documents.MAX_IMAGE_SIDE_PX
        assert {name for name, _ in seen} == {'exif_transpose', 'convert'}
        assert all(max(size) <= documents.MAX_IMAGE_SIDE_PX for _, size in seen)

    @pytest.mark.parametrize('mode', ['P', '1'])
    def test_a_palette_or_bilevel_image_is_shrunk_before_it_is_expanded(self, monkeypatch, mode):
        """Such an image can only be resampled with NEAREST and is expanded to 3-4 bytes a pixel
        to be resampled properly; it must be cut down first, not expanded at full size."""
        expanded = []
        convert = Image.Image.convert

        def spy_convert(image, *args, **kwargs):
            expanded.append(image.size)
            return convert(image, *args, **kwargs)

        monkeypatch.setattr(Image.Image, 'convert', spy_convert)
        out = io.BytesIO()
        Image.new(mode, (6000, 4500)).save(out, format='PNG')

        prepared = documents.prepare_input(out.getvalue(), documents.PNG)

        assert max(Image.open(io.BytesIO(prepared.images[0])).size) == documents.MAX_IMAGE_SIDE_PX
        assert expanded
        assert all(max(size) <= 2 * documents.MAX_IMAGE_SIDE_PX for size in expanded)

    @pytest.mark.parametrize('size', [(300, 200), (4000, 3000)])
    @pytest.mark.parametrize('fmt', ['JPEG', 'PNG'])
    def test_the_exif_orientation_is_applied_to_small_and_large_images(self, size, fmt):
        """Orientation 6 = displayed rotated 90 degrees clockwise: a landscape file becomes
        portrait, and its top-left corner moves to the top-right."""
        exif = Image.Exif()
        exif[0x0112] = 6
        source = Image.new('RGB', size, (255, 255, 255))
        source.paste((255, 0, 0), (0, 0, size[0] // 4, size[1] // 4))  # a red top-left block
        out = io.BytesIO()
        source.save(out, format=fmt, exif=exif)

        prepared = documents.prepare_input(
            out.getvalue(), documents.JPEG if fmt == 'JPEG' else documents.PNG
        )

        result = Image.open(io.BytesIO(prepared.images[0]))
        assert result.height > result.width
        assert max(result.size) == min(max(size), documents.MAX_IMAGE_SIDE_PX)
        corner = result.getpixel((result.width - 5, 5))
        assert isinstance(corner, tuple)
        red, green, blue = corner
        assert red > 200 and green < 80 and blue < 80
        assert result.getpixel((5, result.height - 5)) == (255, 255, 255)

    def test_transparency_is_flattened_onto_white(self):
        prepared = documents.prepare_input(samples.png_with_alpha_bytes(), documents.PNG)

        pixel = Image.open(io.BytesIO(prepared.images[0])).getpixel((5, 5))
        assert isinstance(pixel, tuple)
        assert all(channel > 240 for channel in pixel)

    def test_a_corrupt_image_is_unreadable(self):
        with pytest.raises(documents.UnreadableDocumentError):
            documents.prepare_input(b'\xff\xd8\xff\xe0 not really a jpeg', documents.JPEG)

    def test_an_image_claiming_too_many_pixels_is_refused_before_decoding(self, monkeypatch):
        monkeypatch.setattr(documents, 'MAX_IMAGE_PIXELS', 1000)

        with pytest.raises(documents.DocumentTooComplexError, match='too large'):
            documents.prepare_input(samples.jpeg_bytes((200, 300)), documents.JPEG)

    def test_pillows_process_wide_limit_is_left_alone(self):
        assert Image.MAX_IMAGE_PIXELS != documents.MAX_IMAGE_PIXELS


class TestPdf:
    def test_a_pdf_with_a_text_layer_is_sent_as_text(self):
        prepared = documents.prepare_input(samples.text_pdf_bytes(), documents.PDF)

        assert 'Staples' in prepared.text
        assert '42.10' in prepared.text
        assert prepared.images == []

    def test_a_scanned_pdf_is_rendered_to_images(self):
        prepared = documents.prepare_input(samples.scanned_pdf_bytes(), documents.PDF)

        assert prepared.text == ''
        assert len(prepared.images) == 1
        assert documents.sniff_content_type(prepared.images[0]) == 'image/jpeg'

    def test_only_the_first_pages_of_a_scan_are_rendered(self):
        pages = [Image.new('RGB', (200, 200), 'white') for _ in range(4)]
        out = io.BytesIO()
        pages[0].save(out, format='PDF', save_all=True, append_images=pages[1:])

        prepared = documents.prepare_input(out.getvalue(), documents.PDF, max_pdf_pages=2)

        assert len(prepared.images) == 2

    @pytest.mark.parametrize('size', [(612, 792), (595, 842), (612, 1008), (200, 300)])
    def test_a_normal_page_renders_at_the_full_scale(self, size):
        """Letter, A4, Legal and smaller: unchanged by the longer-side cap."""
        assert documents._render_scale(*size) == documents.PDF_RENDER_SCALE

    @pytest.mark.parametrize('size', [(1224, 1584), (3000, 100), (100, 14_400), (14_400, 14_400)])
    def test_a_page_whose_render_would_be_over_the_ai_size_is_rendered_smaller(self, size):
        """All the AI receives is `MAX_IMAGE_SIDE_PX` on a side, so a poster is rendered at that."""
        scale = documents._render_scale(*size)

        assert 0 < scale < documents.PDF_RENDER_SCALE
        assert max(size) * scale <= documents.MAX_IMAGE_SIDE_PX + 1e-6

    def test_a_pdf_with_huge_pages_is_rendered_no_larger_than_the_ai_size(self, monkeypatch):
        import pypdfium2 as pdfium

        sizes = []
        render = pdfium.PdfPage.render

        def spy_render(page, *args, **kwargs):
            bitmap = render(page, *args, **kwargs)
            sizes.append((bitmap.width, bitmap.height))
            return bitmap

        monkeypatch.setattr(pdfium.PdfPage, 'render', spy_render)

        prepared = documents.prepare_input(samples.blank_pages_pdf_bytes(2, 5000), documents.PDF)

        assert len(prepared.images) == 2
        assert sizes
        assert all(max(size) <= documents.MAX_IMAGE_SIDE_PX for size in sizes)

    def test_a_pdf_stream_that_inflates_far_beyond_a_receipt_is_refused_cheaply(self, monkeypatch):
        """A few-KB PDF whose content stream inflates to 8 MB used to need ~400 MB to read (a
        17 MB one, ~1 GB); under the cap pypdf gives up after 2 MB and nothing is rendered either
        (pdfium decodes such a stream without any limit of its own)."""
        data = samples.inflating_pdf_bytes(8_000_000)
        assert len(data) < 25_000
        monkeypatch.setattr(
            documents, '_render_pdf_pages', lambda *a, **k: pytest.fail('should not render')
        )

        tracemalloc.start()
        try:
            with pytest.raises(documents.DocumentTooComplexError, match='too complex'):
                documents.prepare_input(data, documents.PDF)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert peak < 25_000_000

    def test_the_decompression_cap_is_only_in_force_while_a_pdfs_text_is_read(self):
        from pypdf import get_configuration

        before = get_configuration().zlib_maximum_output_length
        documents._pdf_text(samples.text_pdf_bytes())

        assert before > documents.PDF_TEXT_LIMITS['zlib_maximum_output_length']
        assert get_configuration().zlib_maximum_output_length == before

    def test_a_content_stream_within_the_cap_is_still_read(self):
        text = documents._pdf_text(samples.inflating_pdf_bytes(1_000_000))

        assert text.startswith('x')

    @pytest.mark.parametrize(
        'size', [(14_401, 100), (100, 14_401), (0, 100), (100, -5), (float('nan'), 100)]
    )
    def test_an_absurd_page_size_is_refused(self, size):
        with pytest.raises(documents.DocumentTooComplexError, match='page size'):
            documents._render_scale(*size)

    def test_the_render_is_sized_before_anything_is_allocated(self, monkeypatch):
        """A page that would need a ~3 GB bitmap at the full scale is rendered small."""
        import pypdfium2 as pdfium

        seen = []

        class Page:
            def get_size(self):
                return (14_000, 14_000)

            def render(self, scale):
                seen.append(scale)
                return type('Bitmap', (), {'to_pil': lambda self: Image.new('RGB', (50, 50))})()

            def close(self):
                pass

        class Pdf:
            def __init__(self, data):
                pass

            def __len__(self):
                return 1

            def __getitem__(self, index):
                return Page()

            def close(self):
                pass

        monkeypatch.setattr(pdfium, 'PdfDocument', Pdf)

        [jpeg] = documents._render_pdf_pages(b'%PDF-', 5)

        assert documents.sniff_content_type(jpeg) == 'image/jpeg'
        assert 14_000 * 14_000 * seen[0] ** 2 <= documents.MAX_IMAGE_PIXELS

    def test_a_pdf_with_an_absurd_page_is_unreadable_not_rendered(self, monkeypatch):
        import pypdfium2 as pdfium

        rendered = []

        class Page:
            def get_size(self):
                return (20_000, 20_000)

            def render(self, scale):
                rendered.append(scale)

            def close(self):
                pass

        class Pdf:
            def __init__(self, data):
                pass

            def __len__(self):
                return 1

            def __getitem__(self, index):
                return Page()

            def close(self):
                pass

        monkeypatch.setattr(pdfium, 'PdfDocument', Pdf)

        with pytest.raises(documents.UnreadableDocumentError, match='page size'):
            documents._render_pdf_pages(b'%PDF-', 5)
        assert rendered == []

    def test_a_broken_pdf_is_unreadable(self):
        with pytest.raises(documents.UnreadableDocumentError):
            documents.prepare_input(b'%PDF-1.4\nthis is not a pdf', documents.PDF)


class TestText:
    def test_html_is_reduced_to_its_visible_text(self):
        text = documents.html_to_text(samples.HTML_BODY)

        assert 'Your receipt' in text
        assert 'Staples' in text
        assert 'Total' in text and '42.10' in text
        assert 'alert' not in text
        assert 'color:red' not in text

    def test_html_text_keeps_block_structure(self):
        text = documents.html_to_text('<p>one</p><p>two</p><div>three<br>four</div>')

        assert text.splitlines() == ['one', 'two', 'three', 'four']

    def test_html_entities_are_decoded(self):
        assert documents.html_to_text('<p>Caf&eacute; &amp; Co</p>') == 'Café & Co'

    def test_a_html_body_is_prepared_as_text(self):
        prepared = documents.prepare_input(samples.HTML_BODY, documents.HTML)

        assert 'Staples' in prepared.text
        assert prepared.images == []

    def test_a_plain_text_body_is_prepared_as_text(self):
        prepared = documents.prepare_input('Café 4.50'.encode(), documents.PLAIN)

        assert prepared.text == 'Café 4.50'

    def test_an_empty_body_is_unreadable(self):
        with pytest.raises(documents.UnreadableDocumentError):
            documents.prepare_input(b'<html><script>x</script></html>', documents.HTML)

    def test_text_is_capped(self):
        prepared = documents.prepare_input(b'x ' * 100_000, documents.PLAIN)

        assert len(prepared.text) <= documents.MAX_TEXT_CHARS

    def test_latin1_bodies_are_decoded(self):
        assert documents.decode_text('caf\xe9'.encode('latin-1')) == 'café'


def test_an_unsupported_type_is_refused():
    with pytest.raises(documents.UnsupportedDocumentError):
        documents.prepare_input(b'PK\x03\x04', 'application/zip')


@pytest.fixture(scope='module')
def address_space_limit_enforced():
    """Whether this host enforces `RLIMIT_AS` (Linux does; macOS accepts it and ignores it)."""
    code = (
        'import resource\n'
        'resource.setrlimit(resource.RLIMIT_AS, (300 << 20, 300 << 20))\n'
        'try:\n    bytearray(600 << 20)\nexcept MemoryError:\n    print("enforced")\n'
    )
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    if not sys.platform.startswith('linux') or 'enforced' not in result.stdout:
        pytest.skip('this host does not enforce RLIMIT_AS (only the timeout bounds the child)')


class TestChildProcess:
    """`prepare_input` runs in a child process under a memory and time budget. These tests start
    real children (a few tenths of a second each); the rest of the suite runs in-process."""

    @pytest.fixture
    def sandbox(self, settings):
        settings.RECEIPTS_DOCUMENT_SANDBOX = True
        return settings

    @staticmethod
    def run_command(code: str, *, timeout: float = 20):
        """`_prepare_in_child` with the child replaced by `python -c <code>`."""
        return documents._prepare_in_child(
            b'data',
            documents.PDF,
            max_pdf_pages=5,
            memory_mb=64,
            cpu_seconds=10,
            timeout=timeout,
            command=[sys.executable, '-c', code],
        )

    @pytest.mark.parametrize(
        ('data', 'content_type'),
        [
            (samples.text_pdf_bytes(), documents.PDF),
            (samples.scanned_pdf_bytes(), documents.PDF),
            (samples.jpeg_bytes(), documents.JPEG),
            (samples.heic_bytes(), documents.HEIC),
            (samples.HTML_BODY, documents.HTML),
            (b'Total: 42.10', documents.PLAIN),
        ],
        ids=['text pdf', 'scanned pdf', 'jpeg', 'heic', 'html', 'plain text'],
    )
    def test_the_child_answers_what_the_work_in_process_gives(self, sandbox, data, content_type):
        expected = documents._prepare_unsandboxed(data, content_type)

        prepared = documents.prepare_input(data, content_type)

        assert prepared == expected
        assert (prepared.preview_jpeg is not None) == (content_type == documents.HEIC)

    def test_a_pdf_that_draws_a_form_a_few_times_still_reads(self, sandbox):
        prepared = documents.prepare_input(
            samples.shared_form_pdf_bytes(3, form_bytes=100_000), documents.PDF
        )

        assert len(prepared.images) == 1

    def test_the_sandbox_is_off_when_the_setting_says_so(self, settings, monkeypatch):
        settings.RECEIPTS_DOCUMENT_SANDBOX = False
        monkeypatch.setattr(
            subprocess, 'run', lambda *a, **k: pytest.fail('should not start a process')
        )

        assert documents.prepare_input(b'Total: 42.10', documents.PLAIN).text == 'Total: 42.10'

    def test_an_unsupported_type_is_refused_without_starting_a_child(self, sandbox, monkeypatch):
        monkeypatch.setattr(
            subprocess, 'run', lambda *a, **k: pytest.fail('should not start a process')
        )

        with pytest.raises(documents.UnsupportedDocumentError):
            documents.prepare_input(b'PK\x03\x04', 'application/zip')

    def test_a_files_own_errors_come_back_as_the_same_errors(self, sandbox):
        with pytest.raises(documents.UnreadableDocumentError, match='could not be read') as caught:
            documents.prepare_input(b'%PDF-1.4\nthis is not a pdf', documents.PDF)
        assert not isinstance(caught.value, documents.DocumentTooComplexError)

        with pytest.raises(documents.UnreadableDocumentError, match='empty'):
            documents.prepare_input(b'<html><script>x</script></html>', documents.HTML)

    def test_the_per_stream_cap_comes_back_as_too_complex(self, sandbox):
        with pytest.raises(documents.DocumentTooComplexError, match='too complex'):
            documents.prepare_input(samples.inflating_pdf_bytes(8_000_000), documents.PDF)

    def test_the_child_gets_no_secrets(self, monkeypatch):
        monkeypatch.setenv('OPENAI_API_KEY', 'sk-secret')
        monkeypatch.setenv('RECEIPTS_AWS_SECRET_ACCESS_KEY', 'aws-secret')
        code = (
            'import json, os, sys\n'
            'text = ",".join(sorted(os.environ))\n'
            'ok = {"text": text, "images": [], "preview_jpeg": None}\n'
            'sys.stdout.write(json.dumps({"ok": ok}))'
        )

        names = self.run_command(code).text.split(',')

        assert 'OPENAI_API_KEY' not in names and 'RECEIPTS_AWS_SECRET_ACCESS_KEY' not in names
        assert set(names) <= set(documents._CHILD_ENV_KEYS) | {
            'LC_CTYPE',
            '__CF_USER_TEXT_ENCODING',
        }

    def test_a_child_still_running_at_the_deadline_is_killed_and_the_try_is_retried(
        self, tmp_path, caplog
    ):
        """The wall clock says nothing about the file (a busy host is slow too), so a timeout is a
        `DocumentChildError`, not a permanent `too_large`."""
        pid_file = tmp_path / 'pid'
        code = (
            f'import os, time; open({str(pid_file)!r}, "w").write(str(os.getpid())); time.sleep(60)'
        )
        started = time.monotonic()

        with (
            caplog.at_level(logging.WARNING),
            pytest.raises(documents.DocumentChildError, match='too long') as caught,
        ):
            self.run_command(code, timeout=1)

        assert not isinstance(caught.value, documents.UnreadableDocumentError)
        assert time.monotonic() - started < 15
        assert 'child killed' in caplog.text
        pid = int(pid_file.read_text())
        with pytest.raises(ProcessLookupError):  # gone, and reaped
            os.kill(pid, 0)

    @pytest.mark.parametrize(
        'code',
        [
            'import os, signal; os.kill(os.getpid(), signal.SIGABRT)',  # a failed allocation
            'import os, signal; os.kill(os.getpid(), signal.SIGSEGV)',  # native code gone wrong
            'import os, signal; os.kill(os.getpid(), signal.SIGBUS)',
            'import os, signal; os.kill(os.getpid(), signal.SIGXCPU)',  # the CPU limit
            'import json; print(json.dumps({"error": "too_complex", "message": "x"}))',
        ],
        ids=['SIGABRT', 'SIGSEGV', 'SIGBUS', 'SIGXCPU', 'says too_complex'],
    )
    def test_a_child_that_ends_over_its_budget_is_too_complex(self, code, caplog):
        with caplog.at_level(logging.WARNING), pytest.raises(documents.DocumentTooComplexError):
            self.run_command(code)

        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    @pytest.mark.parametrize(
        'code',
        [
            'import os; os._exit(3)',
            'import sys; sys.exit(1)',  # e.g. a traceback: an import error after a bad deploy
            # The OOM killer: memory pressure on the container or host, not the file (with
            # RLIMIT_AS a file over its budget fails an allocation instead).
            'import os, signal; os.kill(os.getpid(), signal.SIGKILL)',
            'import os, signal; os.kill(os.getpid(), signal.SIGTERM)',  # e.g. an outside stop
            'import os, signal; os.kill(os.getpid(), signal.SIGINT)',  # Ctrl-C reaching the group
            'import os, signal; os.kill(os.getpid(), signal.SIGHUP)',
            'pass',  # no answer at all
            'print("not json")',
            'print("[1, 2]")',
            'print(\'{"ok": {"text": 1}}\')',
            'print(\'{"error": 5}\')',
            'print(\'{"error": "something new"}\')',
            'import json; print(json.dumps({"error": "failed", "message": "ImportError"}))',
        ],
        ids=[
            'exit 3',
            'exit 1',
            'SIGKILL (OOM killer)',
            'SIGTERM',
            'SIGINT',
            'SIGHUP',
            'no output',
            'not json',
            'a list',
            'bad ok',
            'bad error',
            'unknown error',
            'says failed',
        ],
    )
    def test_a_child_that_fails_without_saying_anything_about_the_file_is_retried(
        self, code, caplog
    ):
        """Nothing here is a reason to fail the receipt for good: it is a `DocumentChildError`
        (which `processing` retries), logged as an error."""
        with (
            caplog.at_level(logging.WARNING),
            pytest.raises(documents.DocumentChildError) as caught,
        ):
            self.run_command(code)

        assert not isinstance(caught.value, documents.UnreadableDocumentError)
        [record] = [r for r in caplog.records if r.name == documents.logger.name]
        assert record.levelno == logging.ERROR

    def test_the_log_names_the_signal_or_status_and_never_what_the_child_wrote(self, caplog):
        code = (
            'import os, signal, sys\n'
            'sys.stderr.write("FILE-DERIVED SECRET TEXT " * 1000)\n'
            'sys.stderr.flush()\n'
            'os.kill(os.getpid(), signal.SIGTERM)'
        )

        with caplog.at_level(logging.WARNING), pytest.raises(documents.DocumentChildError):
            self.run_command(code)

        assert 'signal SIGTERM' in caplog.text
        assert 'SECRET' not in caplog.text

    def test_a_noisy_child_does_not_fill_the_workers_memory_or_get_in_the_way(self):
        """stderr is not collected at all: a megabyte of warnings costs the parent nothing."""
        code = (
            'import json, sys\n'
            'sys.stderr.write("x" * 5_000_000)\n'
            'ok = {"text": "fine", "images": [], "preview_jpeg": None}\n'
            'sys.stdout.write(json.dumps({"ok": ok}))'
        )

        assert self.run_command(code).text == 'fine'

    def test_the_child_runs_in_its_own_session(self):
        """A terminal's or process group's signal (Ctrl-C on a local worker) can't reach it."""
        code = (
            'import json, os, sys\n'
            'ok = {"text": str(os.getsid(0) == os.getpid()), "images": [], "preview_jpeg": None}\n'
            'sys.stdout.write(json.dumps({"ok": ok}))'
        )

        assert self.run_command(code).text == 'True'

    def test_a_child_that_cannot_be_started_is_retried(self, caplog):
        with caplog.at_level(logging.ERROR), pytest.raises(documents.DocumentChildError):
            documents._prepare_in_child(
                b'data',
                documents.PDF,
                max_pdf_pages=5,
                memory_mb=64,
                cpu_seconds=10,
                timeout=5,
                command=['/nonexistent/python'],
            )

        assert 'Could not start' in caplog.text

    @pytest.mark.skipif(not sys.platform.startswith('linux'), reason='needs /proc and RLIMIT_CPU')
    def test_a_child_over_its_cpu_limit_ends_with_sigxcpu_long_before_the_wall_clock(self):
        """The CPU limit, not the wall clock, is what refuses a CPU-heavy file."""
        code = (
            'import importlib.util, sys\n'
            f'spec = importlib.util.spec_from_file_location("d", {documents.__file__!r})\n'
            'module = importlib.util.module_from_spec(spec)\n'
            'spec.loader.exec_module(module)\n'
            'module._limit_resources(1024, 1)\n'
            'while True:\n    pass'
        )
        started = time.monotonic()

        with pytest.raises(documents.DocumentTooComplexError):
            documents._prepare_in_child(
                b'data',
                documents.PDF,
                max_pdf_pages=5,
                memory_mb=1024,
                cpu_seconds=1,
                timeout=30,
                command=[sys.executable, '-c', code],
            )

        assert time.monotonic() - started < 15

    @pytest.mark.skipif(not sys.platform.startswith('linux'), reason='needs /proc')
    def test_the_child_asks_to_be_the_oom_killers_first_choice(self):
        code = (
            'import importlib.util\n'
            f'spec = importlib.util.spec_from_file_location("d", {documents.__file__!r})\n'
            'module = importlib.util.module_from_spec(spec)\n'
            'spec.loader.exec_module(module)\n'
            'module._prefer_to_be_oom_killed()\n'
            'print(open("/proc/self/oom_score_adj").read().strip())'
        )

        result = subprocess.run(
            [sys.executable, '-I', '-c', code], capture_output=True, text=True, check=True
        )

        assert result.stdout.strip() == '1000'

    def test_not_being_able_to_set_the_oom_score_is_ignored(self, tmp_path):
        documents._prefer_to_be_oom_killed(str(tmp_path / 'missing' / 'oom_score_adj'))

        score = tmp_path / 'oom_score_adj'
        score.write_text('0')
        documents._prefer_to_be_oom_killed(str(score))
        assert score.read_text() == '1000'

    def test_the_child_is_started_as_an_unprivileged_user_only_by_root(self, monkeypatch):
        import pwd

        monkeypatch.setattr(os, 'geteuid', lambda: 1000)
        assert documents._unprivileged_ids('nobody') == {}

        monkeypatch.setattr(os, 'geteuid', lambda: 0)
        assert documents._unprivileged_ids('') == {}
        assert documents._unprivileged_ids('no-such-user-here') == {}
        entry = pwd.getpwnam('root')  # any user that exists everywhere
        assert documents._unprivileged_ids('root') == {
            'user': entry.pw_uid,
            'group': entry.pw_gid,
            'extra_groups': [],
        }

    def test_the_user_is_passed_to_the_child(self, monkeypatch):
        seen = {}

        def fake_run(*args, **kwargs):
            seen.update(kwargs)
            raise subprocess.TimeoutExpired('x', 1)

        monkeypatch.setattr(documents, '_unprivileged_ids', lambda user: {'user': 65534})
        monkeypatch.setattr(subprocess, 'run', fake_run)

        with pytest.raises(documents.DocumentChildError):
            self.run_command('pass')

        assert seen['user'] == 65534 and seen['start_new_session'] is True
        assert seen['stderr'] == subprocess.DEVNULL

    def test_a_missing_child_user_is_warned_about_once(self, monkeypatch, caplog):
        monkeypatch.setattr(os, 'geteuid', lambda: 0)
        monkeypatch.setattr(documents, '_warned_missing_user', False)

        with caplog.at_level(logging.WARNING):
            assert documents._unprivileged_ids('no-such-user-here') == {}
            assert documents._unprivileged_ids('no-such-user-here') == {}

        assert caplog.text.count('does not exist') == 1

    def test_a_child_that_may_not_switch_user_runs_as_the_worker_instead(self, monkeypatch, caplog):
        """A container without CAP_SETUID/CAP_SETGID: `PermissionError` on the switch."""
        calls = []
        real_run = subprocess.run

        def run(*args, **kwargs):
            calls.append(kwargs.get('user'))
            if 'user' in kwargs:
                raise PermissionError(1, 'Operation not permitted')
            return real_run(*args, **kwargs)

        monkeypatch.setattr(documents, '_unprivileged_ids', lambda user: {'user': 65534})
        monkeypatch.setattr(subprocess, 'run', run)
        code = (
            'import json, sys\n'
            'ok = {"text": "fine", "images": [], "preview_jpeg": None}\n'
            'sys.stdout.write(json.dumps({"ok": ok}))'
        )

        with caplog.at_level(logging.WARNING):
            assert self.run_command(code).text == 'fine'

        assert calls == [65534, None]
        assert 'Could not start the document child' in caplog.text

    def test_a_permission_error_without_a_user_switch_is_not_retried(self, monkeypatch):
        calls = []

        def run(*args, **kwargs):
            calls.append(1)
            raise PermissionError(13, 'Permission denied')

        monkeypatch.setattr(subprocess, 'run', run)

        with pytest.raises(documents.DocumentChildError):
            self.run_command('pass')

        assert calls == [1]

    @pytest.mark.skipif(
        not (sys.platform.startswith('linux') and hasattr(os, 'geteuid') and os.geteuid() == 0),
        reason='the user switch needs Linux and root (as in the deployed image)',
    )
    def test_a_root_worker_really_starts_the_child_as_nobody(self, sandbox):
        import pwd

        code = (
            'import json, os, sys\n'
            'text = f"{os.getuid()} {os.getgid()} {os.getgroups()}"\n'
            'ok = {"text": text, "images": [], "preview_jpeg": None}\n'
            'sys.stdout.write(json.dumps({"ok": ok}))'
        )
        nobody = pwd.getpwnam('nobody')

        prepared = documents._prepare_in_child(
            b'x',
            documents.PLAIN,
            max_pdf_pages=1,
            memory_mb=256,
            cpu_seconds=5,
            timeout=10,
            child_user='nobody',
            command=[sys.executable, '-c', code],
        )

        assert prepared.text == f'{nobody.pw_uid} {nobody.pw_gid} []'
        # and the real child, with the module, venv and libraries, works as that user
        assert documents.prepare_input(b'Total: 42.10', documents.PLAIN).text == 'Total: 42.10'
        assert len(documents.prepare_input(samples.scanned_pdf_bytes(), documents.PDF).images) == 1

    @pytest.mark.parametrize(
        'reply',
        [
            '{"ok": null, "error": "failed", "message": "x"}',
            '{"ok": {"text": "t", "images": ["not base64!"], "preview_jpeg": null}}',
            '{"ok": {"text": "t", "images": [], "preview_jpeg": "***"}}',
        ],
        ids=['ok null', 'bad image base64', 'bad preview base64'],
    )
    def test_a_reply_that_is_not_what_the_protocol_says_is_a_child_failure(self, reply, caplog):
        code = f'import sys; sys.stdout.write({reply!r})'

        with caplog.at_level(logging.ERROR), pytest.raises(documents.DocumentChildError):
            self.run_command(code)

        assert caplog.records

    REQUEST = (
        b'{"content_type": "text/plain", "max_pdf_pages": 5, "memory_mb": 1000, '
        b'"cpu_seconds": 10}\nhello'
    )

    def test_running_out_of_memory_in_the_child_is_too_complex(self, monkeypatch):
        def out_of_memory(*args, **kwargs):
            raise MemoryError

        monkeypatch.setattr(documents, '_prepare_unsandboxed', out_of_memory)

        assert documents._handle_request(self.REQUEST)['error'] == 'too_complex'

    @pytest.mark.parametrize('link', ['__cause__', '__context__'])
    def test_an_error_caused_by_a_memory_error_is_too_complex(self, monkeypatch, link):
        """The readers wrap what a library raises, so the `MemoryError` is further down."""

        def wrapped(*args, **kwargs):
            try:
                raise MemoryError
            except MemoryError as memory_error:
                outer = documents.UnreadableDocumentError('The PDF could not be read: ')
                if link == '__cause__':
                    raise outer from memory_error
                raise outer

        monkeypatch.setattr(documents, '_prepare_unsandboxed', wrapped)
        monkeypatch.setattr(documents, '_peak_address_space', lambda: None)

        assert documents._handle_request(self.REQUEST)['error'] == 'too_complex'

    def test_a_cause_chain_that_loops_does_not_hang(self):
        a, b = RuntimeError('a'), RuntimeError('b')
        a.__cause__, b.__cause__ = b, a

        assert documents._caused_by_memory_error(a) is False

    def test_a_memory_error_inside_pillow_is_too_complex(self, monkeypatch):
        def out_of_memory(*args, **kwargs):
            raise MemoryError

        monkeypatch.setattr(Image.Image, 'load', out_of_memory)

        with pytest.raises(documents.DocumentTooComplexError):
            documents._prepare_guarded(samples.png_bytes(), documents.PNG)

    def test_a_memory_error_inside_pypdf_is_too_complex(self, monkeypatch):
        """`_pdf_text` wraps every exception in `UnreadableDocumentError`; the cause is kept."""
        from pypdf import PdfReader

        def out_of_memory(*args, **kwargs):
            raise MemoryError

        monkeypatch.setattr(PdfReader, 'pages', property(out_of_memory))

        with pytest.raises(documents.DocumentTooComplexError):
            documents._prepare_guarded(samples.text_pdf_bytes(), documents.PDF)

    def test_a_pdfium_failure_after_nearly_all_of_the_budget_is_reported_as_the_budget(
        self, monkeypatch
    ):
        """pdfium reports a failed allocation as its own error, not as a `MemoryError`."""

        def render_failed(*args, **kwargs):
            raise documents._RenderFailedError('The PDF could not be rendered: no memory')

        monkeypatch.setattr(documents, '_prepare_unsandboxed', render_failed)

        for peak_mb, expected in [(1000, 'too_complex'), (900, 'too_complex'), (700, 'unreadable')]:
            monkeypatch.setattr(documents, '_peak_address_space', lambda mb=peak_mb: mb << 20)
            assert documents._handle_request(self.REQUEST)['error'] == expected, peak_mb

    def test_the_peak_does_not_turn_other_unreadable_files_into_over_budget_ones(self, monkeypatch):
        """A corrupt large image can map half the budget; only a pdfium failure near the whole
        budget is taken for running out of it."""

        def corrupt(*args, **kwargs):
            raise documents.UnreadableDocumentError('The image could not be read: truncated')

        monkeypatch.setattr(documents, '_prepare_unsandboxed', corrupt)
        monkeypatch.setattr(documents, '_peak_address_space', lambda: 990 << 20)

        assert documents._handle_request(self.REQUEST)['error'] == 'unreadable'

    def test_a_peak_that_cannot_be_read_does_not_change_the_answer(self, monkeypatch):
        def render_failed(*args, **kwargs):
            raise documents._RenderFailedError('corrupt')

        monkeypatch.setattr(documents, '_prepare_unsandboxed', render_failed)
        monkeypatch.setattr(documents, '_peak_address_space', lambda: None)

        assert documents._handle_request(self.REQUEST)['error'] == 'unreadable'

    def test_an_unforeseen_error_is_the_files_fault_in_the_child_and_in_process_alike(
        self, monkeypatch, settings
    ):
        def broken(*args, **kwargs):
            raise RuntimeError('boom')

        monkeypatch.setattr(documents, '_prepare_unsandboxed', broken)
        monkeypatch.setattr(documents, '_peak_address_space', lambda: None)

        reply = documents._handle_request(self.REQUEST)
        assert reply['error'] == 'unreadable' and 'boom' in reply['message']

        settings.RECEIPTS_DOCUMENT_SANDBOX = False
        with pytest.raises(documents.UnreadableDocumentError, match='boom') as caught:
            documents.prepare_input(b'Total: 42.10', documents.PLAIN)
        assert not isinstance(caught.value, documents.DocumentTooComplexError)

    def test_a_memory_error_is_too_complex_in_process_too(self, monkeypatch, settings):
        def out_of_memory(*args, **kwargs):
            raise MemoryError

        monkeypatch.setattr(documents, '_prepare_unsandboxed', out_of_memory)
        settings.RECEIPTS_DOCUMENT_SANDBOX = False

        with pytest.raises(documents.DocumentTooComplexError):
            documents.prepare_input(b'Total: 42.10', documents.PLAIN)

    def test_a_library_that_cannot_be_imported_is_not_the_files_fault(self, monkeypatch, settings):
        def missing(*args, **kwargs):
            raise ModuleNotFoundError("No module named 'pypdfium2'")

        monkeypatch.setattr(documents, '_prepare_unsandboxed', missing)

        reply = documents._handle_request(self.REQUEST)
        assert reply['error'] == 'failed' and 'pypdfium2' in reply['message']

        settings.RECEIPTS_DOCUMENT_SANDBOX = False
        with pytest.raises(documents.DocumentChildError):
            documents.prepare_input(b'Total: 42.10', documents.PLAIN)

    def test_the_child_as_a_whole_answers_failed_instead_of_dying_on_a_broken_request(self):
        """The real child, fed a request it can't read: it still answers, with a reason that
        names no file content."""
        result = subprocess.run(
            [sys.executable, '-I', documents.__file__],
            input=b'this is not a request',
            capture_output=True,
            check=True,
        )

        reply = documents._parse_reply(result.stdout)
        assert reply == {'error': 'failed', 'message': 'JSONDecodeError'}

    def test_the_real_child_reads_a_file_with_warnings_and_logging_silenced(self):
        """A PDF pypdf complains about (`strict=False` logs and warns instead of raising) reads
        fine, and nothing it logs reaches the parent."""
        data = b'%PDF-1.4\n' + b'1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n' * 3
        result = subprocess.run(
            [sys.executable, '-I', documents.__file__],
            input=(
                b'{"content_type": "application/pdf", "max_pdf_pages": 1, "memory_mb": 1024, '
                b'"cpu_seconds": 10}\n' + data
            ),
            capture_output=True,
            check=True,
        )

        assert documents._parse_reply(result.stdout) is not None
        assert result.stderr == b''

    @pytest.mark.parametrize('distinct_forms', [False, True], ids=['shared form', 'distinct forms'])
    def test_a_tiny_pdf_that_makes_the_renderer_allocate_gigabytes_is_refused(
        self, sandbox, address_space_limit_enforced, distinct_forms
    ):
        """Every stream is under the per-stream cap, but pdfium decodes the form again for each
        use: 40 uses of a 1.9 MB form take ~1.4 GB when rendered with no budget."""
        data = samples.shared_form_pdf_bytes(40, distinct_forms=distinct_forms)
        assert len(data) < 200_000
        started = time.monotonic()

        with pytest.raises(documents.DocumentTooComplexError):
            documents.prepare_input(data, documents.PDF)

        assert time.monotonic() - started < 30
