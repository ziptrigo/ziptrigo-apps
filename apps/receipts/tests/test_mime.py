"""`services.mime`: reading a raw email. Pure functions over bytes (no database), except that the
recipient match reads `settings.RECEIPTS_INBOUND_ADDRESS`."""

import logging
from email.message import Message

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


def strip(raw: bytes, markers) -> bytes:
    return mime.build_stripped_eml(raw, mime.parse_message(raw), markers)


def analyze(raw: bytes, **overrides):
    options = {'allowed_types': ALLOWED, 'max_file_bytes': 15 * 1024**2, 'min_inline_px': 150}
    options.update(overrides)
    return mime.analyze(mime.parse_message(raw), **options)


def with_auth_results(
    results: str,
    *,
    return_path: str | None = 'bounce+x@vendor.com',
    received_from: str | None = 'h.test',
) -> Message:
    """A message with SES's top headers around an `Authentication-Results` value written by hand:
    `Return-Path` and the topmost `Received` agree with `envelope-from=bounce+x@vendor.com;
    helo=h.test` unless told otherwise (`None` leaves the header out)."""
    lines = []
    if return_path is not None:
        lines.append(f'Return-Path: <{return_path}>')
    if received_from is not None:
        lines.append(
            f'Received: from {received_from} ({received_from} [203.0.113.5]) by '
            'inbound-smtp.us-east-1.amazonaws.com with SMTP id abc123 for '
            'receipts+abcd2345@ziptrigo.com; Fri, 09 Oct 2026 12:30:00 +0000'
        )
    lines.append(f'Authentication-Results: {results}')
    return mime.parse_message(('\r\n'.join(lines) + '\r\n\r\nx\r\n').encode())


SPF_OK = (
    'spf=pass (spfCheck: domain of vendor.com designates 203.0.113.5 as permitted sender) '
    'client-ip=203.0.113.5; envelope-from=bounce+x@vendor.com; helo=h.test'
)


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
        """One the sender wrote ends up below the sender-side hops, however SES-like it looks."""
        raw = (
            b'Received: by inbound-smtp.us-east-1.amazonaws.com with SMTP id a for '
            b'receipts+aaaa2222@ziptrigo.com; Fri, 09 Oct 2026 12:30:00 +0000\n'
            b'Received: from mx.sender.test by mail.sender.test; Fri, 09 Oct 2026 12:29:59 +0000\n'
            b'Received: by inbound-smtp.us-east-1.amazonaws.com with SMTP id b for '
            b'receipts+bbbb3333@ziptrigo.com; Fri, 09 Oct 2026 12:29:00 +0000\n\nx\n'
        )

        assert self.addresses(raw) == ['receipts+aaaa2222@ziptrigo.com']

    def test_a_forged_ses_received_header_right_beneath_the_real_one_is_ignored(self):
        """Only the topmost `SES_RECEIVED_HEADER_COUNT` `Received` header(s) are SES's: a copy the
        sender wrote that ends up directly under it is not."""
        raw = (
            b'Received: by inbound-smtp.us-east-1.amazonaws.com with SMTP id a for '
            b'receipts+aaaa2222@ziptrigo.com; Fri, 09 Oct 2026 12:30:00 +0000\n'
            b'Received: by inbound-smtp.us-east-1.amazonaws.com with SMTP id b for '
            b'receipts+bbbb3333@ziptrigo.com; Fri, 09 Oct 2026 12:29:00 +0000\n\nx\n'
        )

        assert self.addresses(raw) == ['receipts+aaaa2222@ziptrigo.com']

    def test_the_number_of_trusted_received_headers_is_one_constant(self, monkeypatch):
        monkeypatch.setattr(mime, 'SES_RECEIVED_HEADER_COUNT', 2)
        raw = (
            b'Received: by inbound-smtp.us-east-1.amazonaws.com with SMTP id a for '
            b'receipts+aaaa2222@ziptrigo.com; Fri, 09 Oct 2026 12:30:00 +0000\n'
            b'Received: by inbound-smtp.us-east-1.amazonaws.com with SMTP id b for '
            b'receipts+bbbb3333@ziptrigo.com; Fri, 09 Oct 2026 12:29:00 +0000\n\nx\n'
        )

        assert self.addresses(raw) == [
            'receipts+aaaa2222@ziptrigo.com',
            'receipts+bbbb3333@ziptrigo.com',
        ]

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
        assert verdicts.spf_pass and verdicts.dkim_pass
        assert verdicts.spf_envelope_from == 'orders@staples.com'
        assert verdicts.dkim_domains == {'staples.com'}
        assert verdicts.dmarc_pass and verdicts.dmarc_from_domain == 'staples.com'

    def test_only_fail_rejects(self):
        assert self.verdicts(spam='FAIL').spam_failed
        assert self.verdicts(virus='FAIL').virus_failed
        for value in ('GRAY', 'PROCESSING_FAILED', 'PASS'):
            verdicts = self.verdicts(spam=value, virus=value)
            assert not verdicts.spam_failed and not verdicts.virus_failed

    def test_spf_and_dkim_are_separate(self):
        both = self.verdicts(spf='PASS', dkim='FAIL')
        assert both.spf_pass and not both.dkim_pass
        both = self.verdicts(spf='FAIL', dkim='PASS')
        assert not both.spf_pass and both.dkim_pass
        both = self.verdicts(spf='FAIL', dkim='GRAY')
        assert not both.spf_pass and not both.dkim_pass

    def test_missing_verdicts_mean_not_authenticated_and_not_flagged(self):
        verdicts = self.verdicts(spam=None, virus=None, spf=None, dkim=None)

        assert not verdicts.spf_pass and not verdicts.dkim_pass
        assert not verdicts.spam_failed and not verdicts.virus_failed

    def test_the_real_ses_header_shape(self):
        """Copied in shape from a message SES stored on staging (infra spike, issue #98): the
        SPF clause's `client-ip`, `envelope-from` and `helo` are separate `;` segments, DKIM has
        `header.i` only, DMARC has `header.from`; `Received-SPF` is there too."""
        verdicts = mime.read_verdicts(
            mime.parse_message((FIXTURES / 'gmail_manual_forward.eml').read_bytes())
        )

        assert verdicts == mime.Verdicts(
            spf_pass=True,
            spf_envelope_from='sender@gmail.com',
            dkim_pass=True,
            dkim_domains=frozenset({'gmail.com'}),
            dmarc_result='pass',
            dmarc_from_domain='gmail.com',
        )

    def test_the_authentication_results_clauses(self):
        def read(results: str) -> mime.Verdicts:
            return mime.read_verdicts(with_auth_results(results))

        verdicts = read(
            'amazonses.com; spf=PASS (c) client-ip=1.2.3.4; envelope-from=Bounce+x@Vendor.COM; '
            'helo=H.test; dkim=pass header.i=@vendor.com; dmarc=pass header.from=Vendor.com;'
        )
        assert verdicts.spf_pass and verdicts.spf_envelope_from == 'bounce+x@vendor.com'
        assert verdicts.dmarc_pass and verdicts.dmarc_from_domain == 'vendor.com'
        # A property belongs to the clause before it: a later method's properties never become the
        # SPF identity or DMARC's domain.
        verdicts = read('amazonses.com; spf=pass; dkim=pass header.d=a.test; dmarc=none')
        assert verdicts.spf_envelope_from == '' and verdicts.dmarc_from_domain == ''
        assert verdicts.dmarc_result == 'none' and not verdicts.dmarc_pass
        # No DMARC / SPF clause at all.
        verdicts = read('amazonses.com; dkim=pass header.d=a.test')
        assert not verdicts.spf_pass and verdicts.dmarc_result == ''
        # Duplicates of SPF or DMARC mean *unauthenticated*, not "the first one wins": a second
        # clause can only be something the sender got into the header.
        for duplicated in (
            'amazonses.com; spf=fail; envelope-from=bounce+x@vendor.com; spf=pass; dkim=pass '
            'header.d=a.test; dmarc=pass header.from=a.test',
            'amazonses.com; spf=pass; dkim=pass header.d=a.test; dmarc=fail header.from=a.test; '
            'dmarc=pass header.from=a.test',
        ):
            verdicts = read(duplicated)
            assert verdicts == mime.Verdicts()

    def test_an_empty_or_none_result_is_just_no_authentication(self, caplog):
        for results in ('amazonses.com;', 'amazonses.com', 'amazonses.com; none'):
            assert mime.read_verdicts(with_auth_results(results)) == mime.Verdicts()
        assert not caplog.records

    def test_the_signing_domain_comes_from_header_d_else_header_i(self):
        def read(results: bytes) -> frozenset[str]:
            raw = b'Authentication-Results: ' + results + b'\n\nx\n'
            return mime.read_verdicts(mime.parse_message(raw)).dkim_domains

        assert read(b'amazonses.com; dkim=pass header.d=Vendor.COM header.i=@mail.vendor.com') == {
            'vendor.com'
        }
        assert read(b'amazonses.com; dkim=pass header.i=@mail.vendor.com') == {'mail.vendor.com'}
        assert read(b'amazonses.com; dkim=pass header.i=bounce@vendor.com') == {'vendor.com'}
        assert read(b'amazonses.com; dkim=pass') == frozenset()
        # Only a signature that passed names a domain.
        assert read(b'amazonses.com; dkim=fail header.d=vendor.com') == frozenset()
        two = read(
            b'amazonses.com; dkim=pass header.d=a.com; dkim=fail header.d=b.com; '
            b'dkim=pass header.d=c.com'
        )
        assert two == {'a.com', 'c.com'}
        # Nonsense is not a domain.
        assert read(b'amazonses.com; dkim=pass header.d=not_a_domain!') == frozenset()

    def test_authentication_results_comments_cannot_fake_a_pass(self):
        raw = (
            b'Authentication-Results: amazonses.com; spf=fail (not dkim=pass spf=pass '
            b'(nested; dkim=pass)) client-ip=1.2.3.4; dkim=none\n\nx\n'
        )

        verdicts = mime.read_verdicts(mime.parse_message(raw))

        assert not verdicts.spf_pass and not verdicts.dkim_pass

    def test_an_authentication_results_header_from_somebody_else_is_ignored(self):
        raw = b'Authentication-Results: mx.evil.test; spf=pass; dkim=pass header.d=x.com\n\nx\n'

        verdicts = mime.read_verdicts(mime.parse_message(raw))

        assert not verdicts.spf_pass and not verdicts.dkim_pass and not verdicts.dkim_domains

    def test_only_the_first_authentication_results_header_counts(self):
        """SES prepends its header: a later one that says `amazonses.com` is the sender's."""
        raw = (
            b'Authentication-Results: mx.evil.test; spf=none\n'
            b'Authentication-Results: amazonses.com; spf=pass; dkim=pass header.d=vendor.com\n'
            b'\nx\n'
        )

        verdicts = mime.read_verdicts(mime.parse_message(raw))

        assert not verdicts.spf_pass and not verdicts.dkim_pass and not verdicts.dkim_domains

    def test_a_later_authentication_results_header_does_not_add_to_the_first(self):
        raw = build_email(
            ses=Ses(spf='FAIL', dkim='FAIL', dmarc='FAIL'),
            headers=[
                (
                    'Authentication-Results',
                    'amazonses.com; spf=pass; envelope-from=x@evil.com; dkim=pass '
                    'header.d=evil.com; dmarc=pass header.from=evil.com',
                )
            ],
        )

        verdicts = mime.read_verdicts(mime.parse_message(raw))

        assert not verdicts.spf_pass and not verdicts.dkim_pass
        assert 'evil.com' not in verdicts.dkim_domains
        assert not verdicts.dmarc_pass and verdicts.dmarc_from_domain != 'evil.com'

    def test_only_the_topmost_spam_and_virus_verdicts_count(self):
        """SES prepends its headers; a copy the sender wrote sits below and must not win."""
        raw = build_email(
            ses=Ses(spam='FAIL', virus='FAIL'),
            headers=[('X-SES-Spam-Verdict', 'PASS'), ('X-SES-Virus-Verdict', 'PASS')],
        )

        verdicts = mime.read_verdicts(mime.parse_message(raw))

        assert verdicts.spam_failed and verdicts.virus_failed

    def test_x_ses_spf_and_dkim_verdicts_are_never_read(self):
        """SES writes neither, so one is the sender's: injected into the message, it authenticates
        nothing -- not with a failing `Authentication-Results`, not
        without one."""
        injected = [('X-SES-Spf-Verdict', 'PASS'), ('X-SES-Dkim-Verdict', 'PASS')]
        message = build_email(ses=Ses(spf='FAIL', dkim='FAIL', dmarc='FAIL'), headers=injected)
        right_below_ses = build_email(
            ses=Ses(spf='FAIL', dkim='FAIL', dmarc='FAIL', extra=injected)
        )
        bare = b'X-SES-Spf-Verdict: PASS\nX-SES-Dkim-Verdict: PASS\n\nx\n'

        for raw in (message, right_below_ses, bare):
            verdicts = mime.read_verdicts(mime.parse_message(raw))

            assert not verdicts.spf_pass and not verdicts.dkim_pass and not verdicts.dmarc_pass
            assert not verdicts.dkim_domains

    def test_received_spf_is_not_trusted(self):
        """A header anyone can write; SES's `Authentication-Results` is the only source."""
        raw = b'Received-SPF: pass (spfCheck: ok) client-ip=1.2.3.4; envelope-from=a@b.test\n\nx\n'
        verdicts = mime.read_verdicts(mime.parse_message(raw))

        assert not verdicts.spf_pass and verdicts.spf_envelope_from == ''

    def test_received_spf_does_not_back_a_failing_authentication_results(self):
        raw = build_email(
            ses=Ses(spf='FAIL', dkim='FAIL'),
            headers=[('Received-SPF', 'pass (spfCheck: ok) envelope-from=orders@staples.com')],
        )

        assert not mime.read_verdicts(mime.parse_message(raw)).spf_pass

    def test_a_forged_authentication_results_below_the_real_one_is_ignored(self):
        """The real (first) header says everything failed; a sender-written copy below it that
        says `amazonses.com` and passes is still the sender's."""
        forged = (
            'amazonses.com; spf=pass; envelope-from=orders@staples.com; dkim=pass '
            'header.d=staples.com; dmarc=pass header.from=staples.com'
        )
        raw = build_email(
            ses=Ses(spf='FAIL', dkim='FAIL', dmarc='FAIL'),
            headers=[('Authentication-Results', forged), ('Authentication-Results', forged)],
        )

        verdicts = mime.read_verdicts(mime.parse_message(raw))

        assert not verdicts.spf_pass and not verdicts.dkim_pass and not verdicts.dmarc_pass
        assert verdicts.dkim_domains == frozenset()
        assert verdicts.spf_envelope_from == 'orders@staples.com'  # the real header's own value


class TestVouchesFor:
    """Authentication has to be about the address that matched: SPF for the envelope sender, an
    aligned DKIM signature for the From address."""

    LISTED = frozenset({'orders@vendor.com', 'billing@vendor.co.uk'})

    def vouches(self, from_addr: str, envelope: str, **ses) -> bool:
        raw = build_email(from_addr=from_addr, ses=Ses(return_path=envelope, **ses))
        verdicts = mime.read_verdicts(mime.parse_message(raw))
        return verdicts.vouches_for(
            from_address=from_addr,
            envelope_sender=envelope,
            is_listed=lambda address: address in self.LISTED,
        )

    def test_spf_pass_vouches_for_a_listed_envelope_sender(self):
        assert self.vouches('x@other.test', 'orders@vendor.com', spf='PASS', dkim='FAIL')

    def test_spf_does_not_vouch_for_a_listed_from_address(self):
        """Pass SPF for your own domain, put somebody else's address in `From`."""
        assert not self.vouches(
            'orders@vendor.com', 'me@evil.test', spf='PASS', dkim='FAIL', dkim_domain=''
        )

    def test_an_aligned_dkim_signature_vouches_for_a_listed_from_address(self):
        assert self.vouches('orders@vendor.com', 'bounce@esp.test', spf='FAIL', dkim='PASS')

    def test_a_subdomain_signature_is_aligned(self):
        assert self.vouches(
            'orders@vendor.com',
            'b@esp.test',
            spf='FAIL',
            dkim='PASS',
            dkim_domain='mail.vendor.com',
        )

    def test_an_unaligned_dkim_signature_does_not_vouch(self):
        assert not self.vouches(
            'orders@vendor.com', 'b@esp.test', spf='FAIL', dkim='PASS', dkim_domain='evil.test'
        )

    def test_without_a_signing_domain_a_from_match_is_not_authenticated(self):
        assert not self.vouches(
            'orders@vendor.com', 'b@esp.test', spf='FAIL', dkim='PASS', dkim_domain=''
        )

    def test_a_failed_dkim_verdict_does_not_vouch_even_with_an_aligned_domain(self):
        assert not self.vouches('orders@vendor.com', 'b@esp.test', spf='FAIL', dkim='FAIL')

    def test_unrelated_names_under_a_two_level_suffix_are_not_aligned(self):
        assert self.vouches(
            'billing@vendor.co.uk',
            'b@esp.test',
            spf='FAIL',
            dkim='PASS',
            dkim_domain='vendor.co.uk',
        )
        assert not self.vouches(
            'billing@vendor.co.uk', 'b@esp.test', spf='FAIL', dkim='PASS', dkim_domain='evil.co.uk'
        )

    def test_a_dmarc_pass_for_the_from_domain_vouches(self):
        assert self.vouches(
            'orders@vendor.com', 'b@esp.test', spf='FAIL', dkim='FAIL', dmarc='PASS'
        )

    def test_a_dmarc_pass_is_compared_to_the_from_domain_case_insensitively(self):
        assert self.vouches(
            'orders@vendor.com', 'b@esp.test', spf='FAIL', dkim='FAIL', dmarc='PASS',
            dmarc_domain='VENDOR.com',
        )  # fmt: skip

    def test_a_dmarc_pass_for_another_domain_does_not_vouch(self):
        """`header.from` has to be exactly the From address's domain -- not a subdomain, not the
        parent, not somebody else's."""
        for domain in ('evil.test', 'mail.vendor.com', 'com'):
            assert not self.vouches(
                'orders@vendor.com', 'b@esp.test', spf='FAIL', dkim='FAIL', dmarc='PASS',
                dmarc_domain=domain,
            )  # fmt: skip

    def test_a_dmarc_result_decides_over_dkim_when_it_is_pass_or_fail(self):
        """An aligned-looking DKIM signature is not second-guessed against a DMARC verdict (e.g.
        `adkim=s` rejecting a subdomain signature)."""
        assert not self.vouches(
            'orders@vendor.com', 'b@esp.test', spf='FAIL', dkim='PASS', dmarc='FAIL'
        )
        assert not self.vouches(
            'orders@vendor.com', 'b@esp.test', spf='FAIL', dkim='PASS', dmarc='PASS',
            dmarc_domain='evil.test',
        )  # fmt: skip

    def test_without_a_dmarc_result_an_aligned_dkim_signature_vouches(self):
        assert self.vouches('orders@vendor.com', 'b@esp.test', spf='FAIL', dkim='PASS', dmarc=None)
        # ... and a domain that publishes no policy (`dmarc=none`) is no worse off.
        assert self.vouches(
            'orders@vendor.com', 'b@esp.test', spf='FAIL', dkim='PASS', dmarc='NONE'
        )
        assert not self.vouches(
            'orders@vendor.com', 'b@esp.test', spf='FAIL', dkim='PASS', dkim_domain='evil.test',
            dmarc=None,
        )  # fmt: skip
        assert not self.vouches(
            'orders@vendor.com', 'b@esp.test', spf='FAIL', dkim='FAIL', dmarc=None
        )

    def test_spf_vouches_only_for_the_address_it_checked(self):
        """The `Return-Path` is listed and SPF passed -- but for a different `envelope-from`."""
        assert self.vouches('x@other.test', 'orders@vendor.com', spf='PASS', dkim='FAIL')
        for checked in ('attacker@evil.test', ''):
            verdicts = mime.Verdicts(spf_pass=True, spf_envelope_from=checked)
            assert not verdicts.vouches_for(
                from_address='x@other.test',
                envelope_sender='orders@vendor.com',
                is_listed=lambda address: address in self.LISTED,
            )

    @pytest.mark.parametrize(
        ('return_path', 'envelope_from', 'same'),
        [
            ('orders@vendor.com', 'orders@vendor.com', True),
            ('Orders@Vendor.COM', 'orders@vendor.com', True),
            ('orders+tag@vendor.com', 'orders@vendor.com', True),
            ('orders@vendor.com', 'ORDERS+other-tag@vendor.com', True),
            ('orders@vendor.com', 'order@vendor.com', False),
            ('orders@vendor.com', 'orders@mail.vendor.com', False),
            ('orders@vendor.com', 'orders@vendor.com.evil.test', False),
            ('o.rders@vendor.com', 'orders@vendor.com', False),
            ('orders@vendor.com', 'prefix+orders@vendor.com', False),
            ('orders@vendor.com', '', False),
        ],
    )
    def test_the_spf_address_is_compared_after_normalization(
        self, return_path, envelope_from, same
    ):
        """Case and a `+tag` are the same address (as in the allowlist); anything else is not."""
        verdicts = mime.Verdicts(spf_pass=True, spf_envelope_from=envelope_from.lower())

        assert verdicts.spf_vouches_for(return_path) is same

    def test_nothing_vouches_for_an_unlisted_address(self):
        assert not self.vouches('x@evil.test', 'x@evil.test')

    def test_an_empty_envelope_sender_and_from_never_vouch(self):
        verdicts = mime.Verdicts(spf_pass=True, dkim_pass=True, dkim_domains=frozenset({''}))

        assert not verdicts.vouches_for(
            from_address='', envelope_sender='', is_listed=lambda address: True
        )


class TestAuthenticationResultsHardening:
    """The header echoes values the sender chose (MAIL FROM, EHLO). Anything but SES's exact shape
    -- structure, value grammars, agreement with `Return-Path` / `Received` -- gives no
    authentication at all."""

    LISTED = frozenset({'billing@victim.com', 'forwarding-noreply@google.com'})
    REAL_TAIL = 'dkim=pass header.i=@attacker.com; dmarc=fail header.from=victim.com;'

    def vouches(self, msg: Message, from_addr: str = 'billing@victim.com') -> bool:
        return mime.read_verdicts(msg).vouches_for(
            from_address=from_addr,
            envelope_sender=mime.envelope_sender(msg),
            is_listed=lambda address: address in self.LISTED,
        )

    # The reviewer's proof of concept: two values the sender controls land in the header SES
    # trusts, and a `;` in them used to start a clause of the sender's choosing.
    INJECTIONS = {
        'quoted-mail-from': (
            'x;dkim=pass header.i=@victim.com;dmarc=pass header.from=victim.com',
            'amazonses.com; spf=pass (spfCheck: domain of attacker.com designates 1.2.3.4 as '
            'permitted sender) client-ip=1.2.3.4; envelope-from="x;dkim=pass '
            'header.i=@victim.com;dmarc=pass header.from=victim.com"@attacker.com; '
            'helo=mail.attacker.com; ',
        ),
        'helo-without-spaces': (
            'h',
            'amazonses.com; spf=pass (c) client-ip=1.2.3.4; envelope-from=x@attacker.com; '
            'helo=x;dkim=pass,header.i=@victim.com;dmarc=pass,header.from=victim.com; ',
        ),
        'rfc-valid-address-literal': (
            'h',
            'amazonses.com; spf=pass (c) client-ip=1.2.3.4; envelope-from=x@attacker.com; '
            'helo=[x:a;dkim=pass,header.i=@victim.com;dmarc=pass,header.from=victim.com;x]; ',
        ),
        'dkim-only-against-a-domain-without-dmarc': (
            'h',
            'amazonses.com; spf=pass (c) client-ip=1.2.3.4; envelope-from=x@attacker.com; '
            'helo=x;dkim=pass,header.i=@victim.com; dkim=pass header.i=@attacker.com; '
            'dmarc=none header.from=victim.com;',
        ),
    }

    @pytest.mark.parametrize('name', list(INJECTIONS))
    def test_injected_clauses_authenticate_nothing(self, name, caplog):
        _, results = self.INJECTIONS[name]
        msg = with_auth_results(
            results if results.rstrip().endswith(';') and 'dmarc=none' in results
            else results + self.REAL_TAIL,
            return_path='x@attacker.com',
            received_from='h',
        )  # fmt: skip

        verdicts = mime.read_verdicts(msg)

        assert verdicts == mime.Verdicts()
        assert not self.vouches(msg)
        assert 'not trusted' in caplog.text

    @pytest.mark.parametrize('name', list(INJECTIONS))
    def test_injected_clauses_fail_even_if_ses_copied_the_values_verbatim(self, name):
        """Even if `Return-Path` and `Received` carry the same full text (SES copying MAIL FROM and
        EHLO unchanged), the grammar refuses the value."""
        mail_from, results = self.INJECTIONS[name]
        helo = results.split('helo=', 1)[1].split('; ', 1)[0]
        results = results if 'dmarc=none' in results else results + self.REAL_TAIL
        return_path = (
            '"x;dkim=pass header.i=@victim.com;dmarc=pass header.from=victim.com"@attacker.com'
            if name == 'quoted-mail-from'
            else 'x@attacker.com'
        )
        msg = with_auth_results(results, return_path=return_path, received_from=helo)

        assert mime.read_verdicts(msg) == mime.Verdicts()
        assert not self.vouches(msg)

    def test_the_same_values_are_harmless_when_they_are_plain(self):
        """The controls: a header of the same shape with ordinary values is accepted, so the
        regressions above fail because of the values and not the scaffolding."""
        msg = with_auth_results(
            'amazonses.com; spf=pass (c) client-ip=203.0.113.5; envelope-from=x@attacker.com; '
            'helo=h.test; dkim=pass header.i=@attacker.com; dmarc=fail header.from=victim.com;',
            return_path='x@attacker.com',
        )

        verdicts = mime.read_verdicts(msg)

        assert verdicts.spf_pass and verdicts.spf_envelope_from == 'x@attacker.com'
        assert verdicts.dmarc_result == 'fail'
        assert not self.vouches(msg)

    @pytest.mark.parametrize(
        'results',
        [
            # a repeated spf / dmarc
            f'amazonses.com; {SPF_OK}; spf=pass; dmarc=pass header.from=victim.com',
            f'amazonses.com; {SPF_OK}; dmarc=fail header.from=victim.com; '
            'dmarc=pass header.from=victim.com',
            # out of order
            f'amazonses.com; dkim=pass header.d=victim.com; {SPF_OK}',
            f'amazonses.com; {SPF_OK}; dmarc=none header.from=victim.com; '
            'dkim=pass header.d=victim.com',
            'amazonses.com; spf=pass client-ip=1.2.3.4; helo=h.test; envelope-from='
            'bounce+x@vendor.com',
            'amazonses.com; spf=pass client-ip=1.2.3.4; helo=h.test; helo=h.test',
            # an unknown segment, method or property
            f'amazonses.com; {SPF_OK}; foo',
            f'amazonses.com; {SPF_OK}; arc=pass',
            f'amazonses.com; {SPF_OK}; dkim=pass header.d=victim.com extra=1',
            f'amazonses.com; {SPF_OK}; dmarc=pass header.from=victim.com header.d=victim.com',
            f'amazonses.com; {SPF_OK};; dkim=pass header.d=victim.com',
            # properties must be separated by whitespace
            f'amazonses.com; {SPF_OK}; dkim=pass,header.d=victim.com',
            f'amazonses.com; {SPF_OK}; dkim=pass header.d=victim.com,header.i=@victim.com',
            # smuggled by a quoted-string, a comment or a stray character
            'amazonses.com; spf=pass client-ip=1.2.3.4; envelope-from="bounce+x"@vendor.com',
            f'amazonses.com; {SPF_OK}; dkim=pass header.d="victim.com"',
            f'amazonses.com; {SPF_OK}; dkim=pass header.d=victim.com\x0bheader.i=@v.test',
            f'amazonses.com; {SPF_OK}; dkim=pass header.d=victim.com (unterminated',
            f'amazonses.com; {SPF_OK}; dkim=pass header.d="victim.com',
        ],
    )
    def test_anything_but_ses_exact_shape_authenticates_nothing(self, results):
        msg = with_auth_results(results)

        assert mime.read_verdicts(msg) == mime.Verdicts()

    @pytest.mark.parametrize(
        ('prop', 'value'),
        [
            ('envelope-from', '"quoted"@vendor.com'),
            ('envelope-from', 'a b@vendor.com'),
            ('envelope-from', 'a,b@vendor.com'),
            ('envelope-from', 'a@vendor.com,b@vendor.com'),
            ('envelope-from', 'a@[1.2.3.4]'),
            ('envelope-from', 'a@under_score.com'),
            ('envelope-from', '@vendor.com'),
            ('envelope-from', 'a@'),
            ('envelope-from', 'a..b@vendor.com'),
            ('helo', 'a,b'),
            ('helo', '[x:a]'),
            ('helo', '[999.1.1.1]'),
            ('helo', '[IPv6:nonsense]'),
            ('helo', '[1.2.3.4'),
            ('helo', '-bad-.test'),
            ('helo', 'a' * 64 + '.test'),
        ],
    )
    def test_the_spf_properties_have_strict_grammars(self, prop, value):
        base = {'envelope-from': 'bounce+x@vendor.com', 'helo': 'h.test'}
        base[prop] = value
        results = (
            f'amazonses.com; spf=pass client-ip=1.2.3.4; envelope-from={base["envelope-from"]}; '
            f'helo={base["helo"]}; dkim=pass header.d=vendor.com; dmarc=pass header.from=vendor.com'
        )
        msg = with_auth_results(
            results, return_path=base['envelope-from'], received_from=base['helo']
        )

        assert mime.read_verdicts(msg) == mime.Verdicts()

    @pytest.mark.parametrize(
        ('prop', 'value'),
        [
            ('header.i', '"a"@vendor.com'),
            ('header.i', 'a b@vendor.com'),
            ('header.i', 'vendor.com'),
            ('header.i', '@vendor.com@evil.test'),
            ('header.d', 'not_a_domain!'),
            ('header.d', '@vendor.com'),
            ('header.d', 'a@vendor.com'),
            ('header.d', 'vendor.com.'),
            ('header.from', 'vendor.com/x'),
            ('header.from', 'a@vendor.com'),
        ],
    )
    def test_the_dkim_and_dmarc_properties_have_strict_grammars(self, prop, value):
        clause = 'dmarc=pass' if prop == 'header.from' else 'dkim=pass'
        msg = with_auth_results(f'amazonses.com; {SPF_OK}; {clause} {prop}={value}')

        assert mime.read_verdicts(msg) == mime.Verdicts()

    @pytest.mark.parametrize(
        ('helo', 'received_from'),
        [
            ('mail-ej1-f49.google.com', 'mail-ej1-f49.google.com'),
            ('Mail.Example.NET', 'mail.example.net'),
            ('under_score.example.net', 'under_score.example.net'),
            ('localhost', 'localhost'),
            ('[203.0.113.5]', '[203.0.113.5]'),
            ('[IPv6:2001:db8::1]', '[IPv6:2001:db8::1]'),
        ],
    )
    def test_plain_helo_hosts_and_address_literals_are_accepted(self, helo, received_from):
        msg = with_auth_results(
            f'amazonses.com; spf=pass client-ip=203.0.113.5; envelope-from=bounce+x@vendor.com; '
            f'helo={helo}; dmarc=pass header.from=vendor.com;',
            received_from=received_from,
        )

        verdicts = mime.read_verdicts(msg)

        assert verdicts.spf_pass and verdicts.dmarc_pass

    def test_semicolons_inside_a_quoted_string_or_a_comment_do_not_split(self):
        assert [
            ' '.join(segment.split())
            for segment in mime._split_resinfo('a; b="x;y(z"; c (d;e) f; g (h "; i) j')
        ] == ['a', 'b="x;y(z"', 'c f', 'g j']
        # A quoted-pair does not end either; an unbalanced one is refused.
        assert mime._split_resinfo(r'a="x\";y"; b (c \) ; d) e') == [r'a="x\";y"', ' b   e']
        for broken in ('a="x', 'a (b', r'a (b \)', 'a="x\\'):
            with pytest.raises(mime._Rejected):
                mime._split_resinfo(broken)

    def test_a_comment_with_semicolons_and_parentheses_in_a_real_header_is_fine(self):
        """SES's own comment may say anything; it is stripped before anything is read."""
        msg = with_auth_results(
            'amazonses.com; spf=pass (spfCheck: domain of vendor.com; (nested) "designates" '
            '203.0.113.5 as permitted sender) client-ip=203.0.113.5; '
            'envelope-from=bounce+x@vendor.com; helo=h.test; dmarc=pass (p=none; sp=none) '
            'header.from=vendor.com;'
        )

        verdicts = mime.read_verdicts(msg)

        assert verdicts.spf_pass and verdicts.dmarc_pass

    def test_a_quoted_string_hides_a_parenthesis_from_the_comment_stripper(self):
        """`"("` is not the start of a comment that swallows the next clause."""
        msg = with_auth_results(
            f'amazonses.com; {SPF_OK}; dkim=pass header.d="(" ; dmarc=pass header.from=victim.com'
        )

        assert mime.read_verdicts(msg) == mime.Verdicts()

    def test_envelope_from_must_be_the_return_path_address(self, caplog):
        results = f'amazonses.com; {SPF_OK}; dmarc=pass header.from=vendor.com;'
        assert mime.read_verdicts(with_auth_results(results)).spf_pass
        # Case does not matter; anything else (a +tag included) does.
        assert mime.read_verdicts(with_auth_results(results, return_path='Bounce+X@Vendor.com'))
        for return_path in ('bounce@vendor.com', 'bounce+x@vendor.com.evil.test', 'other@x.test'):
            assert mime.read_verdicts(with_auth_results(results, return_path=return_path)) == (
                mime.Verdicts()
            )
        assert mime.read_verdicts(with_auth_results(results, return_path=None)) == mime.Verdicts()
        assert mime.read_verdicts(with_auth_results(results, return_path='')) == mime.Verdicts()
        assert 'envelope-from is not the Return-Path address' in caplog.text

    def test_helo_must_be_the_host_of_ses_received_header(self, caplog):
        results = f'amazonses.com; {SPF_OK}; dmarc=pass header.from=vendor.com;'
        for received_from in ('other.test', 'h.test.evil.test', None):
            assert mime.read_verdicts(with_auth_results(results, received_from=received_from)) == (
                mime.Verdicts()
            )
        assert 'helo is not the host' in caplog.text

    def test_a_received_header_that_is_not_ses_does_not_vouch_for_helo(self):
        msg = mime.parse_message(
            b'Return-Path: <bounce+x@vendor.com>\r\n'
            b'Received: from h.test (h.test [1.2.3.4]) by mx.evil.test with SMTP; '
            b'Fri, 09 Oct 2026 12:30:00 +0000\r\n'
            b'Authentication-Results: ' + f'amazonses.com; {SPF_OK};'.encode() + b'\r\n\r\nx\r\n'
        )

        assert mime.read_verdicts(msg) == mime.Verdicts()

    def test_encoded_words_are_not_decoded_before_parsing(self):
        """`=?utf-8?q?...?=` in an echoed value must not turn into a `;` after the fact."""
        msg = with_auth_results(
            'amazonses.com; spf=pass client-ip=1.2.3.4; envelope-from=bounce+x@vendor.com; '
            'helo=h.test; dkim=pass header.d= =?utf-8?q?victim.com=3Bdmarc=3Dpass?=;'
        )

        assert mime.read_verdicts(msg) == mime.Verdicts()

    def test_a_rejection_logs_why_but_not_the_whole_header(self, caplog):
        secret = 'x' * 500
        msg = with_auth_results(f'amazonses.com; {SPF_OK}; dkim=pass header.d={secret}!')

        with caplog.at_level(logging.WARNING, logger=mime.logger.name):
            mime.read_verdicts(msg)

        assert 'Authentication-Results not trusted' in caplog.text
        assert secret not in caplog.text
        assert caplog.records[0].levelno == logging.WARNING

    def test_an_oversized_header_is_refused(self):
        msg = with_auth_results(
            f'amazonses.com; {SPF_OK}; ' + '; '.join(['dkim=pass header.d=a.test'] * 1000)
        )

        assert mime.read_verdicts(msg) == mime.Verdicts()

    def test_the_real_ses_shaped_fixture_authenticates_a_listed_sender(self):
        msg = mime.parse_message((FIXTURES / 'gmail_manual_forward.eml').read_bytes())

        verdicts = mime.read_verdicts(msg)

        assert verdicts.spf_pass and verdicts.dmarc_pass
        for from_addr, envelope in (('sender@gmail.com', ''), ('x@other.test', 'sender@gmail.com')):
            assert verdicts.vouches_for(
                from_address=from_addr,
                envelope_sender=envelope,
                is_listed=lambda address: address == 'sender@gmail.com',
            )

    def test_the_forwarding_confirmation_fixture_still_authenticates(self):
        msg = mime.parse_message((FIXTURES / 'gmail_forwarding_confirmation.eml').read_bytes())

        assert mime.read_verdicts(msg).vouches_for(
            from_address=mime.from_address(msg),
            envelope_sender=mime.envelope_sender(msg),
            is_listed=mime.is_forwarding_confirmation_sender,
        )


class TestDkimFallback:
    """The DKIM-only path (no DMARC verdict) has no organizational-domain list: the signing domain
    is the From domain or one is under the other."""

    LISTED = frozenset({'billing@victim.github.io', 'orders@vendor.com', 'orders@mail.vendor.com'})

    def vouches(self, from_addr: str, **ses) -> bool:
        raw = build_email(from_addr=from_addr, ses=Ses(return_path='x@esp.test', **ses))
        msg = mime.parse_message(raw)
        return mime.read_verdicts(msg).vouches_for(
            from_address=from_addr,
            envelope_sender='',
            is_listed=lambda address: address in self.LISTED,
        )

    def test_a_sibling_under_a_shared_suffix_is_not_aligned(self):
        assert not self.vouches(
            'billing@victim.github.io',
            spf='FAIL',
            dkim='PASS',
            dkim_domain='attacker.github.io',
            dmarc='NONE',
        )

    @pytest.mark.parametrize('signer', ['vendor.com', 'mail.vendor.com'])
    def test_the_same_domain_or_a_subdomain_signer_is_aligned(self, signer):
        assert self.vouches(
            'orders@vendor.com', spf='FAIL', dkim='PASS', dkim_domain=signer, dmarc='NONE'
        )

    def test_a_parent_domain_signer_is_aligned(self):
        assert self.vouches(
            'orders@mail.vendor.com',
            spf='FAIL',
            dkim='PASS',
            dkim_domain='vendor.com',
            dmarc=None,
        )

    @pytest.mark.parametrize('dmarc', ['TEMPERROR', 'PERMERROR', 'POLICY', 'FAIL'])
    def test_a_dmarc_error_or_failure_is_final(self, dmarc):
        """No fallback to an aligned DKIM signature unless SES had no DMARC verdict, or `none`."""
        assert not self.vouches(
            'orders@vendor.com', spf='FAIL', dkim='PASS', dkim_domain='vendor.com', dmarc=dmarc
        )

    @pytest.mark.parametrize(
        ('signer', 'domain', 'aligned'),
        [
            ('vendor.com', 'vendor.com', True),
            ('VENDOR.com.', 'vendor.com', True),
            ('mail.vendor.com', 'vendor.com', True),
            ('vendor.com', 'mail.vendor.com', True),
            ('a.vendor.com', 'b.vendor.com', False),
            ('attacker.github.io', 'victim.github.io', False),
            ('evil.com', 'vendor.com', False),
            ('vendor.com.evil.com', 'vendor.com', False),
            ('notvendor.com', 'vendor.com', False),
            ('evil.co.uk', 'vendor.co.uk', False),
            ('com', 'vendor.com', False),
            ('vendor.com', 'com', False),
            ('', 'vendor.com', False),
            ('vendor.com', '', False),
        ],
    )
    def test_strictly_aligned(self, signer, domain, aligned):
        assert mime.strictly_aligned(signer, domain) is aligned


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

    def test_a_malformed_link_is_skipped_not_a_crash(self):
        """`urlsplit` raises `ValueError` on `https://[x`; that used to fail the whole message."""
        raw = build_email(
            text='Confirmation code: 123456\nhttps://[x\nhttps://[::1\n'
            'https://mail-settings.google.com/mail/vf-ok\n',
        )

        code, link = mime.extract_forwarding_confirmation(mime.parse_message(raw))

        assert (code, link) == ('123456', 'https://mail-settings.google.com/mail/vf-ok')

    def test_a_message_with_only_a_malformed_link_has_no_link(self):
        raw = build_email(text='Confirmation code: 123456\nhttps://[x\n')

        assert mime.extract_forwarding_confirmation(mime.parse_message(raw)) == ('123456', '')

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

        stripped = strip(raw, markers)

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

    def test_the_tree_it_is_given_is_not_modified(self):
        """One parse serves every recipient: stripping works on a copy."""
        raw = build_email(attachments=[pdf()])
        parsed = mime.parse_message(raw)
        index = analyze(raw).attachments[0].index

        mime.build_stripped_eml(raw, parsed, {index: (mime.SKIPPED_HEADER, 'x')})

        [attachment] = [p for p in mime.iter_leaves(parsed) if mime.is_attachment_like(p)]
        assert mime.part_bytes(attachment) == samples.text_pdf_bytes()
        assert attachment['X-Receipts-Skipped'] is None

    def test_a_message_that_cannot_be_rebuilt_keeps_its_headers(self, monkeypatch):
        raw = build_email(attachments=[pdf()])
        monkeypatch.setattr(mime.copy, 'deepcopy', lambda _msg: 1 / 0)

        stripped = strip(raw, {})

        assert stripped.startswith(b'Return-Path:') and b'X-Receipts-Stripped' in stripped
        assert b'JVBERi0' not in stripped  # no attachment data

    def test_a_big_text_part_that_is_not_the_receipt_is_emptied(self):
        """A 5 MB HTML body next to a tiny PDF: the PDF is the receipt, and the stored copy must
        not carry the body (the user pays for it)."""
        big = '<html><body>' + 'x' * (5 * 1024 * 1024) + '</body></html>'
        raw = build_email(html=big, attachments=[pdf()])
        index = analyze(raw).attachments[0].index

        stripped = strip(raw, {index: (mime.RECEIPT_ID_HEADER, 'rid')})

        assert len(stripped) < 20 * 1024
        msg = mime.parse_message(stripped)
        html_part = msg.get_body(preferencelist=('html',))  # ty: ignore[unresolved-attribute]
        assert html_part is not None
        assert html_part['X-Receipts-Skipped'] == mime.SKIP_TOO_LARGE
        assert mime.part_text(html_part).strip() == ''
        # The small plain-text part is kept.
        assert b'Thanks for your order' in stripped

    def test_text_parts_under_the_limit_are_kept(self):
        raw = build_email(html=HTML_RECEIPT)

        stripped = strip(raw, {})

        assert b'Thanks for your order' in stripped and b'X-Receipts-Skipped' not in stripped

    def test_a_sender_written_marker_header_does_not_survive(self):
        raw = build_email(attachments=[pdf()])
        parsed = mime.parse_message(raw)
        [attachment] = [p for p in mime.iter_leaves(parsed) if mime.is_attachment_like(p)]
        attachment['X-Receipts-Receipt-Id'] = 'forged'
        index = analyze(raw).attachments[0].index

        stripped = mime.build_stripped_eml(
            raw, parsed, {index: (mime.SKIPPED_HEADER, 'inline image')}
        )

        assert b'forged' not in stripped

    def test_over_the_total_ceiling_only_the_headers_are_kept(self, monkeypatch):
        raw = build_email(html=HTML_RECEIPT, subject='Order')
        monkeypatch.setattr(mime, 'MAX_EML_BYTES', 1200)

        stripped = strip(raw, {})

        assert stripped.startswith(b'Return-Path:') and b'Subject: Order' in stripped
        assert b'X-Receipts-Stripped: headers only' in stripped
        assert b'Thanks for your order' not in stripped
        assert len(stripped) < 1200

    def test_leaves_past_the_cap_and_parts_below_the_depth_limit_are_emptied(self, monkeypatch):
        monkeypatch.setattr(mime, 'MAX_LEAVES', 2)
        raw = build_email(
            text='first text part of the email, long enough',
            attachments=[
                Attachment('a.txt', b'secret-one', 'text', 'plain'),
                Attachment('b.txt', b'secret-two', 'text', 'plain'),
            ],
        )

        stripped = strip(raw, {})

        assert b'secret-two' not in stripped  # the third leaf is over the cap
        assert b'X-Receipts-Skipped: ' + mime.SKIP_OVER_LIMIT.encode() in stripped

        monkeypatch.setattr(mime, 'MAX_LEAVES', 300)
        monkeypatch.setattr(mime, 'MAX_DEPTH', 1)
        nested = build_email(
            text='cover note for the forward, long enough',
            nested=build_email(
                attachments=[Attachment('deep.txt', b'deep-secret', 'text', 'plain')]
            ),
        )

        stripped = strip(nested, {})

        assert b'deep-secret' not in stripped


class TestLimits:
    def multipart(self, parts: int) -> bytes:
        body = b''.join(b'--b\r\n\r\nx\r\n' for _ in range(parts))
        return (
            b'Content-Type: multipart/mixed; boundary=b\r\nSubject: bomb\r\n\r\n'
            + body
            + b'--b--\r\n'
        )

    def test_a_message_with_too_many_parts_is_refused_before_parsing(self):
        assert mime.exceeds_part_limit(self.multipart(mime.MAX_BOUNDARY_LINES + 1))
        assert not mime.exceeds_part_limit(self.multipart(mime.MAX_BOUNDARY_LINES - 10))

    def test_the_scan_is_fast_on_a_huge_bomb(self):
        import time

        raw = b'--b\r\n\r\nx\r\n' * 2_000_000  # ~20 MB of parts

        started = time.monotonic()
        assert mime.exceeds_part_limit(raw)
        assert time.monotonic() - started < 2

    def test_ordinary_dashes_are_not_parts(self):
        raw = b'Subject: report\n\n' + b'-----\n' * 5000 + b'-- \n' * 5000 + b'--- -- \n' * 5000

        assert not mime.exceeds_part_limit(raw)

    def test_the_fixtures_and_a_forward_are_far_below_the_limit(self):
        for name in ('pdf_attachment.eml', 'forward_as_attachment.eml', 'html_body.eml'):
            assert not mime.exceeds_part_limit((FIXTURES / name).read_bytes())

    def test_leaves_are_capped(self, monkeypatch):
        monkeypatch.setattr(mime, 'MAX_LEAVES', 5)
        raw = build_email(
            attachments=[Attachment(f'{i}.txt', b'x', 'text', 'plain') for i in range(10)]
        )

        assert len(list(mime.iter_leaves(mime.parse_message(raw)))) == 5

    def test_data_of_a_skipped_part_is_not_kept(self):
        junk = Attachment('x.bin', b'MZ' + b'\x00' * 1000, 'application', 'octet-stream')
        analysis = analyze(build_email(attachments=[junk, pdf()]), max_file_bytes=100_000)
        [skipped, eligible] = analysis.attachments

        assert skipped.skip == mime.SKIP_UNSUPPORTED
        assert skipped.data == b'' and skipped.size == 1002
        assert eligible.data == samples.text_pdf_bytes()

    def test_the_body_is_not_analysed_when_an_attachment_is_eligible(self):
        assert analyze(build_email(html=HTML_RECEIPT, attachments=[pdf()])).body is None


def nested_forwards(levels: int, bottom: bytes | None = None) -> bytes:
    raw = bottom or build_email(
        text='The actual receipt: total 42.10 USD, order R-55102, thank you.'
    )
    for _ in range(levels):
        raw = build_email(text='FYI, see below.', nested=raw)
    return raw


class TestDeepNesting:
    """A message forwarded inside a forwarded message inside ...: both tree walkers share one
    depth measure, so a crafted nest can no longer make `analyze` raise."""

    @pytest.mark.parametrize('levels', [1, 5, 12, 20, 25, 40])
    def test_analyze_never_raises(self, levels):
        analysis = analyze(nested_forwards(levels))

        assert analysis.attachments == []  # nothing attached anywhere
        # Whatever was within reach is fine; the point is that it finishes.

    def test_a_shallow_nest_still_finds_the_innermost_body(self):
        analysis = analyze(nested_forwards(3))

        assert analysis.body is not None and b'The actual receipt' in analysis.body.data

    def test_the_regression_input_really_has_a_leaf_outside_the_main_walk(self):
        """Why `_choose_body` used to raise `KeyError`: restarted from a deep nested message, the
        per-message walk reaches a leaf the top-level walk (and so its index) never saw."""
        msg = mime.parse_message(nested_forwards(25))
        in_reach = {id(p) for p in mime.iter_leaves(msg)}

        outside = [
            leaf
            for candidate in mime.iter_messages(msg)
            for leaf in mime.iter_leaves(candidate, into_messages=False)
            if id(leaf) not in in_reach
        ]

        assert outside
        assert (
            mime.analyze(
                msg, allowed_types=ALLOWED, max_file_bytes=1024, min_inline_px=150
            ).attachments
            == []
        )

    def test_an_attachment_inside_a_deep_nest_is_not_a_crash(self):
        inner = build_email(attachments=[pdf('inner.pdf')])

        analysis = analyze(nested_forwards(22, inner))

        assert all(a.skip == '' for a in analysis.eligible)


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
