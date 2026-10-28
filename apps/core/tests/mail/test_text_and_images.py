"""The parts of `apps.core.services.mail` added for To-do (issue #186): the HTML-to-text with
links, the image-size reader, and `analyze` with `allowed_types=None` and no body chooser."""

from typing import Any

import pytest

from apps.core.services.mail import mime
from apps.core.services.mail.images import IMAGE_TYPES, image_dimensions
from apps.core.services.mail.text import html_to_text
from apps.core.services.sniff import SNIFF_BYTES, sniff_inline_type
from apps.receipts.tests import samples
from apps.receipts.tests.emails import HTML_RECEIPT, Attachment, build_email, logo, pdf

pytestmark = [pytest.mark.unit]


def sniff(data: bytes) -> str | None:
    return sniff_inline_type(data[:SNIFF_BYTES])


def analyze(raw: bytes, **overrides):
    options: dict[str, Any] = {
        'allowed_types': None,
        'max_file_bytes': 10**7,
        'min_inline_image_px': 150,
        'sniff': sniff,
    }
    options.update(overrides)
    return mime.analyze(mime.parse_message(raw), **options)


class TestHtmlToText:
    def test_blocks_become_lines_and_whitespace_collapses(self):
        assert html_to_text('<h1>Title</h1><p>one   two</p><div>three</div>') == (
            'Title\none two\nthree'
        )

    def test_scripts_styles_and_the_head_are_dropped(self):
        html = '<head><title>t</title></head><style>x{}</style><p>kept<script>bad()</script></p>'

        assert html_to_text(html) == 'kept'

    def test_list_items(self):
        assert html_to_text('<ul><li>a</li><li>b</li></ul>') == '- a\n- b'

    def test_a_link_shows_its_address_after_its_text(self):
        assert html_to_text('<a href="https://x.test/a?b=1">the page</a>') == (
            'the page (https://x.test/a?b=1)'
        )

    def test_a_link_without_text_is_its_address(self):
        assert html_to_text('<a href="https://x.test/"><img src="a.png"></a>') == 'https://x.test/'

    def test_a_link_that_is_its_own_text_is_not_repeated(self):
        assert html_to_text('<a href="https://x.test/">https://x.test/</a>') == 'https://x.test/'
        assert html_to_text('<a href="mailto:a@b.test">a@b.test</a>') == 'a@b.test'

    @pytest.mark.parametrize('href', ['javascript:alert(1)', 'data:text/html,x', '/rel', '#top'])
    def test_other_links_keep_only_their_text(self, href):
        assert html_to_text(f'<a href="{href}">go</a>') == 'go'

    def test_bytes_and_the_cap(self):
        assert html_to_text('<p>héllo</p>'.encode()) == 'héllo'
        assert html_to_text('<p>' + 'a' * 100 + '</p>', max_chars=10) == 'a' * 10

    def test_broken_html_never_raises(self):
        assert html_to_text('<p>open <b>never closed <a href="https://x.test"') is not None


class TestImages:
    def test_dimensions_are_read_from_the_header(self):
        assert image_dimensions(samples.png_bytes((120, 80))) == (120, 80)
        assert image_dimensions(samples.jpeg_bytes((30, 40))) == (30, 40)

    def test_garbage_is_none(self):
        assert image_dimensions(b'not an image') is None
        assert image_dimensions(samples.png_bytes()[:30]) is None

    def test_the_types(self):
        assert {'image/png', 'image/jpeg'} <= IMAGE_TYPES


class TestAnalyzeForAnyType:
    def test_none_accepts_every_type_even_an_unrecognised_one(self):
        raw = build_email(
            attachments=[
                pdf(),
                Attachment('notes.txt', b'plain notes', 'text', 'plain'),
                Attachment('blob.bin', b'\x00\x01\x02', 'application', 'octet-stream'),
            ]
        )

        analysis = analyze(raw)

        assert [a.filename for a in analysis.eligible] == ['invoice.pdf', 'notes.txt', 'blob.bin']
        assert [a.content_type for a in analysis.eligible] == ['application/pdf', '', '']

    def test_a_list_still_filters(self):
        raw = build_email(attachments=[pdf(), Attachment('b.bin', b'\x00\x01', 'application', 'x')])

        analysis = analyze(raw, allowed_types=['application/pdf'])

        assert [a.filename for a in analysis.eligible] == ['invoice.pdf']
        assert analysis.attachments[1].skip == mime.SKIP_UNSUPPORTED

    def test_an_empty_part_and_an_oversized_one_are_still_skipped(self):
        raw = build_email(
            attachments=[Attachment('e.bin', b'', 'application', 'x'), pdf()],
        )

        analysis = analyze(raw, max_file_bytes=50)

        assert analysis.attachments[0].skip == mime.SKIP_EMPTY
        assert analysis.too_large and analysis.eligible == []

    def test_tiny_inline_images_are_skipped_only_when_the_reader_is_given(self):
        raw = build_email(text=None, html=HTML_RECEIPT, attachments=[logo((48, 48))])

        assert len(analyze(raw).eligible) == 1
        assert (
            analyze(raw, image_dimensions=image_dimensions, image_types=IMAGE_TYPES).eligible == []
        )

    def test_no_body_is_chosen_without_a_converter(self):
        raw = build_email(text=None, html=HTML_RECEIPT)

        assert analyze(raw).body is None
        assert analyze(raw, html_to_text=html_to_text).body is not None
