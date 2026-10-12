import io

import pytest
from PIL import Image

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

        with pytest.raises(documents.UnreadableDocumentError, match='too large'):
            documents.prepare_input(samples.jpeg_bytes((200, 300)), documents.JPEG)

    def test_pillow_is_told_the_pixel_limit(self):
        assert Image.MAX_IMAGE_PIXELS == documents.MAX_IMAGE_PIXELS


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
