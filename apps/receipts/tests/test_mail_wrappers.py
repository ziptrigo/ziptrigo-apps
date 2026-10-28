"""Receipts' `services.mime` and `services.addresses` are thin wrappers over
`apps.core.services.mail` (issue #186): they bind Receipts' parameters. The parser itself is tested
in `apps/core/tests/mail/`."""

import pytest

from apps.core.services.mail import addresses as core_addresses
from apps.core.services.mail import mime as core_mime

from ..services import addresses, mime
from .emails import FIXED_ADDRESS, HTML_RECEIPT, Ses, build_email, logo, pdf

pytestmark = [pytest.mark.unit]


def test_the_wrappers_re_export_the_core_names():
    for name in ('parse_message', 'Verdicts', 'read_verdicts', 'clean_text', 'MAX_PARTS'):
        assert getattr(mime, name) is getattr(core_mime, name)
    assert addresses.parse_allowlist is core_addresses.parse_allowlist
    assert addresses.matches_allowlist is core_addresses.matches_allowlist


def test_recipients_are_looked_up_under_the_receipts_address(settings):
    settings.RECEIPTS_INBOUND_ADDRESS = 'receipts-staging@ziptrigo.com'
    msg = mime.parse_message(build_email(ses=Ses(recipient='receipts+abcd2345@ziptrigo.com')))

    assert mime.scan_recipients(msg).matches == []
    settings.RECEIPTS_INBOUND_ADDRESS = 'receipts@ziptrigo.com'
    [match] = mime.scan_recipients(msg).matches
    assert match.address == FIXED_ADDRESS
    [explicit] = mime.recipients_from_addresses(['Receipts+ABCD2345@ziptrigo.com']).matches
    assert explicit.address == FIXED_ADDRESS
    assert addresses.inbox_code_from_address(FIXED_ADDRESS) == 'abcd2345'
    assert addresses.inbox_code_from_address('todo+abcd2345@ziptrigo.com') is None


def test_analyze_binds_the_receipts_sniffer_converter_and_image_reader():
    raw = build_email(text=None, html=HTML_RECEIPT, attachments=[logo((48, 48))])

    analysis = mime.analyze(
        mime.parse_message(raw),
        allowed_types=['application/pdf', 'image/png'],
        max_file_bytes=10**7,
        min_inline_px=150,
    )

    # The tiny inline logo is skipped (image reader) and the body is chosen (HTML converter).
    assert analysis.eligible == [] and analysis.body is not None
    assert analysis.body.content_type == 'text/html'
    pdf_analysis = mime.analyze(
        mime.parse_message(build_email(attachments=[pdf()])),
        allowed_types=['application/pdf'],
        max_file_bytes=10**7,
        min_inline_px=150,
    )
    assert [a.content_type for a in pdf_analysis.eligible] == ['application/pdf']


def test_the_stripped_copy_is_marked_with_the_receipts_headers():
    raw = build_email(attachments=[pdf()])
    parsed = mime.parse_message(raw)
    index = (
        mime.analyze(
            parsed, allowed_types=['application/pdf'], max_file_bytes=10**7, min_inline_px=150
        )
        .attachments[0]
        .index
    )

    stripped = mime.build_stripped_eml(raw, parsed, {index: (mime.RECEIPT_ID_HEADER, 'rid')})

    assert b'X-Receipts-Receipt-Id: rid' in stripped
    assert mime.RECEIPT_ID_HEADER == 'X-Receipts-Receipt-Id'
    assert mime.SKIPPED_HEADER == 'X-Receipts-Skipped'
    assert mime.STRIPPED_HEADER == 'X-Receipts-Stripped'


def test_a_headers_only_copy_says_so_with_the_receipts_header(monkeypatch):
    monkeypatch.setattr(core_mime, 'MAX_EML_BYTES', 10)
    raw = build_email(attachments=[pdf()])

    stripped = mime.build_stripped_eml(raw, mime.parse_message(raw), {})

    assert b'X-Receipts-Stripped: headers only' in stripped
