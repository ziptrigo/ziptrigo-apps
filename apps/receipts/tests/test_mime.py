"""`services.mime`: reading a raw email. Pure functions over bytes (no database), except that the
recipient match reads `settings.RECEIPTS_INBOUND_ADDRESS`."""

import pytest

from ..models import DEFAULT_ALLOWED_TYPES
from ..services import mime
from . import samples
from .emails import (
    FIXED_ADDRESS,
    FIXTURES,
    HTML_RECEIPT,
    Attachment,
    Ses,
    build_email,
    jpeg,
    logo,
    pdf,
)

pytestmark = [pytest.mark.unit]

ALLOWED = DEFAULT_ALLOWED_TYPES.split(',')


def analyze(raw: bytes, **overrides):
    options = {'allowed_types': ALLOWED, 'max_file_bytes': 15 * 1024**2, 'min_inline_px': 150}
    options.update(overrides)
    return mime.analyze(mime.parse_message(raw), **options)


class TestRecipients:
    def addresses(self, raw: bytes) -> list[str]:
        return [r.address for r in mime.scan_recipients(mime.parse_message(raw)).matches]

    def test_the_envelope_recipient_from_the_ses_received_header(self):
        raw = build_email(to='joao@gmail.com')

        assert self.addresses(raw) == [FIXED_ADDRESS]

    def test_a_received_header_that_ses_did_not_write_is_ignored(self):
        """A sender can write any `Received ... for` they like; only SES's counts."""
        raw = build_email(
            ses=Ses(recipient='someone@else.com'),
            headers=[('Received', 'from evil by mx.evil.test for receipts+zzzz2222@ziptrigo.com;')],
        )

        assert self.addresses(raw) == []

    def test_a_forged_ses_received_header_below_a_real_hop_is_ignored(self):
        """Only the leading run of SES's own `Received` headers counts: one the sender wrote ends
        up below the sender-side hops, however SES-like it looks."""
        raw = (
            b'Received: by inbound-smtp.us-east-1.amazonaws.com with SMTP id a for '
            b'receipts+aaaa2222@ziptrigo.com; Fri, 09 Oct 2026 12:30:00 +0000\n'
            b'Received: from mx.sender.test by mail.sender.test; Fri, 09 Oct 2026 12:29:59 +0000\n'
            b'Received: by inbound-smtp.us-east-1.amazonaws.com with SMTP id b for '
            b'receipts+bbbb3333@ziptrigo.com; Fri, 09 Oct 2026 12:29:00 +0000\n\nx\n'
        )

        assert self.addresses(raw) == ['receipts+aaaa2222@ziptrigo.com']

    def test_falls_back_to_delivered_to_then_x_original_to_then_to_and_cc(self):
        base = b'Subject: x\n\nbody\n'
        cases = [
            (b'Delivered-To: receipts+aaaa2222@ziptrigo.com\n' + base, 'aaaa2222'),
            (b'X-Original-To: receipts+bbbb3333@ziptrigo.com\n' + base, 'bbbb3333'),
            (b'To: Me <receipts+cccc4444@ziptrigo.com>\n' + base, 'cccc4444'),
            (b'To: a@b.com\nCc: receipts+dddd5555@ziptrigo.com\n' + base, 'dddd5555'),
        ]
        for raw, code in cases:
            assert self.addresses(raw) == [f'receipts+{code}@ziptrigo.com']

    def test_the_first_source_with_an_inbox_address_wins(self):
        raw = build_email(
            to='receipts+zzzz9999@ziptrigo.com',
            headers=[('Delivered-To', 'receipts+yyyy8888@ziptrigo.com')],
        )

        assert self.addresses(raw) == [FIXED_ADDRESS]

    def test_several_recipients_and_duplicates(self):
        raw = (
            b'Delivered-To: receipts+aaaa2222@ziptrigo.com\n'
            b'Delivered-To: RECEIPTS+bbbb3333@ZipTrigo.com\n'
            b'Delivered-To: receipts+aaaa2222@ziptrigo.com\n\nx\n'
        )

        assert self.addresses(raw) == [
            'receipts+aaaa2222@ziptrigo.com',
            'receipts+bbbb3333@ziptrigo.com',
        ]

    def test_addresses_that_are_not_inbox_addresses_do_not_match(self):
        for address in (
            'receipts@ziptrigo.com',  # no code
            'receipts+@ziptrigo.com',
            'receipts+abcd2345@example.com',  # another domain
            'other+abcd2345@ziptrigo.com',  # another local part
            'receipts+ab cd@ziptrigo.com',
        ):
            assert self.addresses(f'Delivered-To: {address}\n\nx\n'.encode()) == []

    def test_explicit_addresses(self):
        scan = mime.recipients_from_addresses(['Receipts+ABCD2345@ziptrigo.com', 'x@y.com'])

        assert [(r.code, r.address) for r in scan.matches] == [('abcd2345', FIXED_ADDRESS)]


class TestVerdicts:
    def verdicts(self, **ses) -> mime.Verdicts:
        return mime.read_verdicts(mime.parse_message(build_email(ses=Ses(**ses))))

    def test_a_clean_authenticated_message(self):
        verdicts = self.verdicts()

        assert not verdicts.spam_failed and not verdicts.virus_failed
        assert verdicts.spf_pass and verdicts.dkim_pass and verdicts.authenticated

    def test_only_fail_rejects(self):
        assert self.verdicts(spam='FAIL').spam_failed
        assert self.verdicts(virus='FAIL').virus_failed
        for value in ('GRAY', 'PROCESSING_FAILED', 'PASS'):
            verdicts = self.verdicts(spam=value, virus=value)
            assert not verdicts.spam_failed and not verdicts.virus_failed

    def test_either_spf_or_dkim_authenticates(self):
        assert self.verdicts(spf='PASS', dkim='FAIL').authenticated
        assert self.verdicts(spf='FAIL', dkim='PASS').authenticated
        assert not self.verdicts(spf='FAIL', dkim='GRAY').authenticated

    def test_missing_verdicts_mean_not_authenticated_and_not_flagged(self):
        verdicts = self.verdicts(spam=None, virus=None, spf=None, dkim=None)

        assert not verdicts.authenticated
        assert not verdicts.spam_failed and not verdicts.virus_failed

    def test_falls_back_to_the_authentication_results_header(self):
        verdicts = self.verdicts(spf='PASS', dkim='FAIL', via_authentication_results=True)

        assert verdicts.spf_pass and not verdicts.dkim_pass

    def test_authentication_results_comments_cannot_fake_a_pass(self):
        raw = (
            b'Authentication-Results: amazonses.com; spf=fail (not dkim=pass spf=pass) '
            b'smtp.mailfrom=x; dkim=none\n\nx\n'
        )

        verdicts = mime.read_verdicts(mime.parse_message(raw))

        assert not verdicts.authenticated

    def test_an_authentication_results_header_from_somebody_else_is_ignored(self):
        raw = b'Authentication-Results: mx.evil.test; spf=pass; dkim=pass\n\nx\n'

        assert not mime.read_verdicts(mime.parse_message(raw)).authenticated

    def test_only_the_topmost_verdict_header_counts(self):
        """SES prepends its headers; a copy the sender wrote sits below and must not win."""
        raw = build_email(
            ses=Ses(spf='FAIL', dkim='FAIL'),
            headers=[('X-SES-Spf-Verdict', 'PASS'), ('X-SES-Dkim-Verdict', 'PASS')],
        )

        assert not mime.read_verdicts(mime.parse_message(raw)).authenticated

    def test_received_spf_is_the_last_resort(self):
        raw = b'Received-SPF: pass (spfCheck: ok) client-ip=1.2.3.4\n\nx\n'

        assert mime.read_verdicts(mime.parse_message(raw)).spf_pass


class TestSenders:
    def test_from_and_envelope_sender(self):
        raw = build_email(
            from_addr='"Staples, Inc." <Orders@Staples.com>', ses=Ses(return_path='bounce@x.com')
        )
        msg = mime.parse_message(raw)

        assert mime.from_address(msg) == 'Orders@Staples.com'
        assert mime.envelope_sender(msg) == 'bounce@x.com'

    def test_the_null_envelope_sender(self):
        msg = mime.parse_message(b'Return-Path: <>\nFrom: a@b.com\n\nx\n')

        assert mime.envelope_sender(msg) == ''

    def test_undecodable_bytes_in_a_header_cannot_reach_the_database(self):
        msg = mime.parse_message(b'Subject: caf\xe9 \x00receipt\n\nx\n')

        subject = mime.subject(msg)

        subject.encode('utf-8')  # would raise on a lone surrogate
        assert '\x00' not in subject and 'receipt' in subject

    def test_subject_is_decoded_and_cleaned(self):
        msg = mime.parse_message(b'Subject: =?utf-8?q?Caf=C3=A9_receipt?=\n\nx\n')

        assert mime.subject(msg) == 'Café receipt'

    def test_received_at_is_the_ses_date_and_never_in_the_future(self):
        msg = mime.parse_message(build_email())
        future = mime.parse_message(b'Date: Mon, 01 Jan 2040 00:00:00 +0000\n\nx\n')

        assert mime.received_at(msg).isoformat().startswith('2026-10-09T12:30')
        assert mime.received_at(future).year < 2040
        assert mime.received_at(mime.parse_message(b'\nx\n')).year >= 2026


class TestForwardingConfirmation:
    def test_gmails_code_and_link(self):
        from .emails import gmail_forwarding_confirmation

        msg = mime.parse_message(gmail_forwarding_confirmation())

        code, link = mime.extract_forwarding_confirmation(msg)

        assert code == '987654321'
        assert link == 'https://mail-settings.google.com/mail/vf-%5BANGjdJ8Xk%5D-AbCdEf123'

    def test_a_link_to_another_host_is_never_taken(self):
        raw = build_email(
            text='Confirmation code: 123456\nhttps://evil.test/mail-settings.google.com/x\n'
            'http://mail-settings.google.com/not-https\n'
            'https://mail-settings.google.com.evil.test/x\n',
        )

        code, link = mime.extract_forwarding_confirmation(mime.parse_message(raw))

        assert (code, link) == ('123456', '')

    def test_the_code_falls_back_to_the_subject(self):
        raw = build_email(subject='(#55512345) Gmail Forwarding Confirmation', text='hello')

        assert mime.extract_forwarding_confirmation(mime.parse_message(raw))[0] == '55512345'

    def test_known_senders_are_matched_normalized(self):
        assert mime.is_forwarding_confirmation_sender('Forwarding-Noreply@Google.com')
        assert not mime.is_forwarding_confirmation_sender('noreply@google.com', 'a@b.com')
        assert mime.is_forwarding_confirmation_sender('x@y.com', 'forwarding-noreply@google.com')


class TestAnalyze:
    def test_a_pdf_attachment_is_eligible(self):
        analysis = analyze(build_email(attachments=[pdf()]))

        [item] = analysis.eligible
        assert item.filename == 'invoice.pdf'
        assert item.content_type == 'application/pdf'
        assert item.data == samples.text_pdf_bytes()

    def test_the_sniffed_type_decides_not_the_declared_one_or_the_name(self):
        fake = Attachment('invoice.pdf', b'MZ\x90\x00 not a pdf at all', 'application', 'pdf')
        disguised = Attachment('virus.exe', samples.jpeg_bytes(), 'application', 'octet-stream')

        analysis = analyze(build_email(attachments=[fake, disguised]))

        assert [(a.filename, a.skip) for a in analysis.attachments] == [
            ('invoice.pdf', mime.SKIP_UNSUPPORTED),
            ('virus.exe', ''),
        ]
        assert analysis.eligible[0].content_type == 'image/jpeg'

    def test_a_type_that_is_not_allowed_by_the_settings_is_skipped(self):
        analysis = analyze(
            build_email(attachments=[pdf(), jpeg()]), allowed_types=['application/pdf']
        )

        assert [a.skip for a in analysis.attachments] == ['', mime.SKIP_UNSUPPORTED]

    def test_empty_attachments_are_skipped(self):
        analysis = analyze(build_email(attachments=[Attachment('a.pdf', b'')]))

        assert analysis.attachments[0].skip == mime.SKIP_EMPTY

    def test_an_attachment_over_the_limit_is_flagged_too_large(self):
        analysis = analyze(build_email(attachments=[pdf()]), max_file_bytes=100)

        assert analysis.eligible == []
        assert [a.filename for a in analysis.too_large] == ['invoice.pdf']

    def test_a_small_cid_image_in_the_html_is_a_logo(self):
        raw = build_email(text=None, html=HTML_RECEIPT, attachments=[logo((48, 48))])

        analysis = analyze(raw)

        assert [a.skip for a in analysis.attachments] == [mime.SKIP_INLINE]

    def test_a_big_cid_image_is_kept(self):
        raw = build_email(text=None, html=HTML_RECEIPT, attachments=[logo((400, 300))])

        assert len(analyze(raw).eligible) == 1

    def test_one_long_side_is_enough_to_keep_an_image(self):
        raw = build_email(text=None, html=HTML_RECEIPT, attachments=[logo((600, 40))])

        assert len(analyze(raw).eligible) == 1

    def test_a_small_image_the_html_does_not_reference_is_kept(self):
        tiny = Attachment('photo.png', samples.png_bytes((48, 48)), 'image', 'png')

        assert len(analyze(build_email(attachments=[tiny])).eligible) == 1

    def test_the_inline_threshold_is_the_setting(self):
        raw = build_email(text=None, html=HTML_RECEIPT, attachments=[logo((100, 100))])

        assert analyze(raw, min_inline_px=150).eligible == []
        assert len(analyze(raw, min_inline_px=50).eligible) == 1

    def test_an_image_whose_size_cannot_be_read_is_not_assumed_to_be_decoration(self):
        broken = Attachment('logo.png', samples.png_bytes()[:30], 'image', 'png', cid='logo@vendor')
        raw = build_email(text=None, html=HTML_RECEIPT, attachments=[broken])

        assert len(analyze(raw).eligible) == 1

    def test_attachments_inside_a_forwarded_message_are_found(self):
        inner = build_email(attachments=[pdf('inner.pdf')])
        raw = build_email(text='See attached.', nested=inner)

        analysis = analyze(raw)

        assert [a.filename for a in analysis.eligible] == ['inner.pdf']

    def test_the_html_body_is_preferred_over_the_text_one(self):
        analysis = analyze(build_email(html=HTML_RECEIPT))

        assert analysis.body is not None
        assert analysis.body.content_type == 'text/html'
        assert b'Staples' in analysis.body.data

    def test_a_text_only_body(self):
        analysis = analyze(build_email())

        assert analysis.body is not None and analysis.body.content_type == 'text/plain'

    def test_a_body_with_almost_no_text_is_not_a_receipt(self):
        assert analyze(build_email(text='ok thanks')).body is None
        assert (
            analyze(build_email(text=None, html='<html><body><img src=x></body></html>')).body
            is None
        )

    def test_an_html_body_with_no_text_falls_back_to_the_text_part(self):
        raw = build_email(html='<html><body><img src="x"></body></html>')

        assert analyze(raw).body.content_type == 'text/plain'

    def test_the_forwarded_email_beats_the_cover_note(self):
        inner = build_email(
            text='Staples order R-77. Total: 12.00 USD. Thank you for shopping with us.'
        )
        raw = build_email(text='FYI, please file this one for me, thanks!!', nested=inner)

        body = analyze(raw).body

        assert body is not None and b'Staples order R-77' in body.data

    def test_a_text_file_attachment_is_not_the_body(self):
        raw = build_email(
            text=None,
            html=None,
            attachments=[Attachment('notes.txt', b'x' * 100, 'text', 'plain')],
        )

        assert analyze(raw).body is None

    def test_a_non_utf8_charset_is_decoded(self):
        raw = (
            b'Content-Type: text/plain; charset=iso-8859-1\nContent-Transfer-Encoding: 8bit\n\n'
            b'Caf\xe9 receipt: total 3.50 EUR, thank you for your visit\n'
        )

        body = analyze(raw).body

        assert body is not None and 'Café receipt' in body.data.decode()


class TestStrippedEml:
    def test_payloads_are_emptied_and_marked_and_everything_else_survives(self):
        raw = build_email(
            html=HTML_RECEIPT, attachments=[pdf(), jpeg(), logo((40, 40))], subject='Order'
        )
        analysis = analyze(raw)
        by_name = {a.filename: a.index for a in analysis.attachments}
        markers = {
            by_name['invoice.pdf']: (mime.RECEIPT_ID_HEADER, 'rid-1'),
            by_name['photo.jpg']: (mime.SKIPPED_HEADER, mime.SKIP_DAILY_LIMIT),
            by_name['logo.png']: (mime.SKIPPED_HEADER, mime.SKIP_INLINE),
        }

        stripped = mime.build_stripped_eml(raw, markers)

        assert len(stripped) < len(raw) / 2
        msg = mime.parse_message(stripped)
        assert mime.subject(msg) == 'Order'
        assert mime.from_address(msg) == 'orders@staples.com'
        assert msg.get('X-SES-Spam-Verdict') == 'PASS'
        parts = {p.get_filename(): p for p in mime.iter_leaves(msg) if mime.is_attachment_like(p)}
        assert parts['invoice.pdf']['X-Receipts-Receipt-Id'] == 'rid-1'
        assert parts['invoice.pdf']['X-Receipts-Skipped'] is None
        assert parts['photo.jpg']['X-Receipts-Skipped'] == 'daily limit'
        assert parts['logo.png']['X-Receipts-Skipped'] == 'inline image'
        parts = list(parts.values())
        assert all(mime.part_bytes(p) == b'' for p in parts)
        assert samples.text_pdf_bytes()[:40] not in stripped
        # The body text parts are untouched.
        assert b'Thanks for your order' in stripped

    def test_a_message_that_cannot_be_rebuilt_keeps_its_headers(self, monkeypatch):
        raw = build_email(attachments=[pdf()])
        monkeypatch.setattr(mime, 'parse_message', lambda _raw: 1 / 0)

        stripped = mime.build_stripped_eml(raw, {})

        assert stripped.startswith(b'Return-Path:') and b'X-Receipts-Stripped' in stripped
        assert b'JVBERi0' not in stripped  # no attachment data


def test_the_literal_fixtures_parse_and_match_their_builders():
    """The checked-in `.eml` files are what `write_fixtures()` writes."""
    for name in (
        'pdf_attachment.eml',
        'html_body.eml',
        'gmail_forwarding_confirmation.eml',
        'gmail_auto_forward.eml',
        'forward_as_attachment.eml',
    ):
        msg = mime.parse_message((FIXTURES / name).read_bytes())
        assert [r.address for r in mime.scan_recipients(msg).matches] == [FIXED_ADDRESS]
