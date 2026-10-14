"""Builds raw inbound emails the way SES stores them in S3 (shapes confirmed on staging, issue #98's
"Spike results" comment): its own headers on top (`Return-Path`, the `Received ... for <recipient>`
line, `X-SES-Spam/Virus-Verdict`, `Received-SPF`, and one `Authentication-Results: amazonses.com`
with the SPF, DKIM and DMARC results), then the sender's message. SES writes **no**
`X-SES-Spf-Verdict` / `X-SES-Dkim-Verdict`, so neither does this; a test that needs one (a forged
copy) passes it in `headers=`/`Ses.extra`.

Used by the inbound tests, and to (re)generate the literal `.eml` files in `fixtures/`
(`write_fixtures()`), so the files and the tests cannot drift apart.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime
from pathlib import Path

from apps.receipts.services.mime import strictly_aligned

from . import samples

FIXTURES = Path(__file__).parent / 'fixtures'
FIXED_CODE = 'abcd2345'
FIXED_ADDRESS = f'receipts+{FIXED_CODE}@ziptrigo.com'
RECEIVED_DATE = datetime(2026, 10, 9, 12, 30, tzinfo=UTC)
_RAW_FROM_PLACEHOLDER = 'raw-from-placeholder@placeholder.invalid'


@dataclass
class Attachment:
    filename: str
    data: bytes
    maintype: str = 'application'
    subtype: str = 'pdf'
    cid: str | None = None  # make it an inline image of the HTML body
    disposition: str = 'attachment'


def pdf(filename: str = 'invoice.pdf') -> Attachment:
    return Attachment(filename, samples.text_pdf_bytes(), 'application', 'pdf')


def jpeg(filename: str = 'photo.jpg', size: tuple[int, int] = (200, 300)) -> Attachment:
    return Attachment(filename, samples.jpeg_bytes(size), 'image', 'jpeg')


def png(filename: str = 'scan.png', size: tuple[int, int] = (200, 300)) -> Attachment:
    return Attachment(filename, samples.png_bytes(size), 'image', 'png')


def logo(size: tuple[int, int] = (48, 48), cid: str = 'logo@vendor') -> Attachment:
    return Attachment(
        'logo.png', samples.png_bytes(size), 'image', 'png', cid=cid, disposition='inline'
    )


@dataclass
class Ses:
    """The headers SES adds. Defaults are a clean, authenticated message."""

    recipient: str = FIXED_ADDRESS
    spam: str | None = 'PASS'
    virus: str | None = 'PASS'
    #: The SPF result in `Authentication-Results` (and `Received-SPF`); `None` leaves it out.
    spf: str | None = 'PASS'
    dkim: str | None = 'PASS'
    return_path: str | None = None  # default: the From address
    #: The address SPF was checked for (`envelope-from=`); default: the `Return-Path` address.
    spf_envelope_from: str | None = None
    #: The domain a passing DKIM signature was made for (`header.i=@<domain>` in SES's
    #: `Authentication-Results`, as on real mail; no `header.d`): default, the domain of the From
    #: address (an aligned signature); `''` leaves it out.
    dkim_domain: str | None = None
    #: The DMARC result. `'auto'` (default): pass when the DKIM signature, or SPF on the envelope
    #: sender, is aligned with the From domain, else fail -- what a real evaluation would say.
    #: Otherwise the literal value (`'PASS'`, `'FAIL'`, `'NONE'`); `None` leaves the clause out.
    dmarc: str | None = 'auto'
    #: `header.from=` of the DMARC clause; default: the domain of the From address.
    dmarc_domain: str | None = None
    #: Where the message was handed to SES from.
    client_ip: str = '203.0.113.5'
    helo: str = 'mail.example.net'
    #: The host in the `from` of SES's `Received` header; default: the `helo` (SES writes what the
    #: sender said). A different value is a message whose two headers disagree.
    received_from: str | None = None
    extra: list[tuple[str, str]] = field(default_factory=list)


def _auth_results(ses: Ses, *, from_domain: str, return_path: str) -> str:
    """The `Authentication-Results` value as SES writes it: the SPF clause, with `client-ip`,
    `envelope-from` and `helo` as separate `;` segments after it, then DKIM, then DMARC."""
    parts = ['amazonses.com']
    if ses.spf is not None:
        envelope_from = ses.spf_envelope_from if ses.spf_envelope_from is not None else return_path
        spf = ses.spf.lower()
        parts.append(
            f'spf={spf} (spfCheck: domain of {return_path.rpartition("@")[2]} designates '
            f'{ses.client_ip} as permitted sender) client-ip={ses.client_ip}'
        )
        parts.append(f'envelope-from={envelope_from}')
        parts.append(f'helo={ses.helo}')
    dkim_domain = from_domain if ses.dkim_domain is None else ses.dkim_domain
    if ses.dkim is not None:
        parts.append(
            f'dkim={ses.dkim.lower()}' + (f' header.i=@{dkim_domain}' if dkim_domain else '')
        )
    dmarc = ses.dmarc
    if dmarc == 'auto':
        aligned = (ses.dkim == 'PASS' and strictly_aligned(dkim_domain, from_domain)) or (
            ses.spf == 'PASS' and strictly_aligned(return_path.rpartition('@')[2], from_domain)
        )
        dmarc = 'PASS' if aligned else 'FAIL'
    if dmarc is not None:
        header_from = from_domain if ses.dmarc_domain is None else ses.dmarc_domain
        parts.append(f'dmarc={dmarc.lower()} header.from={header_from}')
    return '; '.join(parts) + ';'


def build_email(
    *,
    from_addr: str = 'Staples <orders@staples.com>',
    to: str | None = None,
    subject: str = 'Your Staples receipt',
    text: str
    | None = 'Thanks for your order. Total 42.10 USD. Order R-55102 placed on 2026-10-09.',
    html: str | None = None,
    attachments: list[Attachment] | None = None,
    ses: Ses | None = None,
    headers: list[tuple[str, str]] | None = None,
    nested: bytes | None = None,
    raw_from: str | None = None,
) -> bytes:
    """`raw_from`, if given, is written verbatim as the value of the `From:` line (it replaces
    `from_addr`). Needed to put on the wire what `EmailMessage` would never write itself: setting
    `msg['From']` decodes an RFC 2047 encoded word and writes it unquoted
    (`x<a@b.test>@evil.test`), which is not what a sender's raw
    `=?utf-8?q?x=3Ca=40b.test=3E?=@evil.test` looks like to the parser."""
    ses = ses or Ses()
    msg = EmailMessage()
    if raw_from is not None:
        from_addr = raw_from
    bare_from = from_addr.rpartition('<')[2].rstrip('>') or from_addr
    bare_return_path = ses.return_path if ses.return_path is not None else bare_from
    msg['Return-Path'] = f'<{bare_return_path}>'
    msg['Received'] = (
        f'from {ses.received_from or ses.helo} ({ses.helo} [{ses.client_ip}]) by '
        f'inbound-smtp.us-east-1.amazonaws.com with SMTP id abc123def456 for {ses.recipient}; '
        f'{format_datetime(RECEIVED_DATE)}'
    )
    if ses.spam is not None:
        msg['X-SES-Spam-Verdict'] = ses.spam
    if ses.virus is not None:
        msg['X-SES-Virus-Verdict'] = ses.virus
    if ses.spf is not None:
        msg['Received-SPF'] = (
            f'{ses.spf.lower()} (spfCheck: ok) client-ip={ses.client_ip}; '
            f'envelope-from={bare_return_path}; helo={ses.helo};'
        )
    msg['Authentication-Results'] = _auth_results(
        ses, from_domain=bare_from.rpartition('@')[2], return_path=bare_return_path
    )
    for name, value in ses.extra:
        msg[name] = value

    msg['From'] = _RAW_FROM_PLACEHOLDER if raw_from is not None else from_addr
    msg['To'] = to or ses.recipient
    msg['Subject'] = subject
    msg['Date'] = format_datetime(RECEIVED_DATE)
    msg['Message-ID'] = '<fixture-1@example.net>'
    for name, value in headers or []:
        msg[name] = value

    if text is not None:
        msg.set_content(text)
        if html is not None:
            msg.add_alternative(html, subtype='html')
    elif html is not None:
        msg.set_content(html, subtype='html')
    else:
        msg.set_content('')

    for attachment in attachments or []:
        if attachment.cid:
            body = msg.get_body(preferencelist=('html',))
            assert body is not None, 'an inline image needs an HTML body'
            body.add_related(
                attachment.data,
                attachment.maintype,
                attachment.subtype,
                cid=f'<{attachment.cid}>',
                disposition=attachment.disposition,
                filename=attachment.filename,
            )
        else:
            msg.add_attachment(
                attachment.data,
                maintype=attachment.maintype,
                subtype=attachment.subtype,
                filename=attachment.filename,
                disposition=attachment.disposition,
            )
    if nested is not None:
        # `add_attachment` wants a message object: re-parse the bytes.
        msg.add_attachment(BytesParser(policy=policy.default).parsebytes(nested))
    data = msg.as_bytes()
    if raw_from is not None:
        line = f'From: {_RAW_FROM_PLACEHOLDER}'.encode()
        assert data.count(line) == 1
        data = data.replace(line, f'From: {raw_from}'.encode('utf-8'))
    return data


HTML_RECEIPT = (
    '<html><body><img src="cid:logo@vendor"><h1>Staples</h1><p>Thanks for your order.</p>'
    '<table><tr><td>Total</td><td>42.10 USD</td></tr></table><p>Order R-55102, 2026-10-09</p>'
    '</body></html>'
)


def gmail_forwarding_confirmation(recipient: str = FIXED_ADDRESS) -> bytes:
    body = (
        f'joao@gmail.com has requested to automatically forward mail to your email address '
        f'{recipient}.\n\nConfirmation code: 987654321\n\nTo allow joao@gmail.com to '
        f'automatically forward mail to your address, please click the link below to confirm the '
        f'request:\n\nhttps://mail-settings.google.com/mail/vf-%5BANGjdJ8Xk%5D-AbCdEf123\n\n'
        f'If you click the link and it appears to be broken, please copy and paste it into a new '
        f'browser window. If you do not approve of this request, no further action is required.\n'
    )
    return build_email(
        from_addr='Gmail Team <forwarding-noreply@google.com>',
        subject='(#987654321) Gmail Forwarding Confirmation - Receive Mail from joao@gmail.com',
        text=body,
        ses=Ses(recipient=recipient, return_path='forwarding-noreply@google.com'),
    )


def gmail_auto_forward(recipient: str = FIXED_ADDRESS) -> bytes:
    """A vendor's receipt that a user's Gmail forwarded: `To:` is the user's own address, the
    inbox address is only in SES's `Received ... for`, and the envelope sender is Gmail's
    `+caf_=` rewrite of the original one."""
    return build_email(
        from_addr='Staples <orders@staples.com>',
        to='joao@gmail.com',
        subject='Your Staples receipt',
        html=HTML_RECEIPT,
        attachments=[pdf('staples-receipt.pdf')],
        ses=Ses(
            recipient=recipient,
            return_path='orders+caf_=joao=gmail.com@staples.com',
            spf='FAIL',  # forwarding breaks SPF; DKIM survives
            dkim='PASS',
            extra=[('Delivered-To', 'joao@gmail.com')],
        ),
        text=None,
    )


def gmail_manual_forward(recipient: str = FIXED_ADDRESS) -> bytes:
    """A receipt the user forwarded by hand from Gmail, with the headers exactly as SES wrote them
    on real staging mail (the infra spike): `Authentication-Results` with `envelope-from` as its
    own segment, DKIM with `header.i` only, DMARC for `gmail.com`, plus `Received-SPF`."""
    return build_email(
        from_addr='Sender Example <sender@gmail.com>',
        subject='Fwd: Your receipt',
        html=HTML_RECEIPT,
        text=None,
        attachments=[logo()],
        ses=Ses(
            recipient=recipient,
            client_ip='209.85.218.49',
            helo='mail-ej1-f49.google.com',
            dmarc='PASS',
        ),
    )


def write_fixtures() -> None:
    """Regenerate the literal `.eml` files in `fixtures/` (run by hand after changing a builder)."""
    FIXTURES.mkdir(exist_ok=True)
    files = {
        'pdf_attachment.eml': build_email(attachments=[pdf()]),
        'html_body.eml': build_email(text=None, html=HTML_RECEIPT, attachments=[logo()]),
        'gmail_forwarding_confirmation.eml': gmail_forwarding_confirmation(),
        'gmail_auto_forward.eml': gmail_auto_forward(),
        'gmail_manual_forward.eml': gmail_manual_forward(),
        'forward_as_attachment.eml': build_email(
            from_addr='joao@gmail.com',
            to=FIXED_ADDRESS,
            subject='Fwd: Your Staples receipt',
            text='See attached.',
            nested=build_email(attachments=[pdf('inner-invoice.pdf')]),
        ),
    }
    for name, data in files.items():
        (FIXTURES / name).write_bytes(data)


if __name__ == '__main__':  # pragma: no cover
    write_fixtures()
