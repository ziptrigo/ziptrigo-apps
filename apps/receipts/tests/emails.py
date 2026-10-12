"""Builds raw inbound emails the way SES stores them in S3: its own headers on top (`Return-Path`,
the `Received ... for <recipient>` line, the verdict headers), then the sender's message.

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

from . import samples

FIXTURES = Path(__file__).parent / 'fixtures'
FIXED_CODE = 'abcd2345'
FIXED_ADDRESS = f'receipts+{FIXED_CODE}@ziptrigo.com'
RECEIVED_DATE = datetime(2026, 10, 9, 12, 30, tzinfo=UTC)


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
    spf: str | None = 'PASS'
    dkim: str | None = 'PASS'
    return_path: str | None = None  # default: the From address
    #: Use `Authentication-Results` instead of the `X-SES-Spf/Dkim-Verdict` headers.
    via_authentication_results: bool = False
    extra: list[tuple[str, str]] = field(default_factory=list)


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
) -> bytes:
    ses = ses or Ses()
    msg = EmailMessage()
    bare_from = from_addr.rpartition('<')[2].rstrip('>') or from_addr
    msg['Return-Path'] = f'<{ses.return_path if ses.return_path is not None else bare_from}>'
    msg['Received'] = (
        f'from mail.example.net (mail.example.net [203.0.113.5]) by '
        f'inbound-smtp.us-east-1.amazonaws.com with SMTP id abc123def456 for {ses.recipient}; '
        f'{format_datetime(RECEIVED_DATE)}'
    )
    if ses.spam is not None:
        msg['X-SES-Spam-Verdict'] = ses.spam
    if ses.virus is not None:
        msg['X-SES-Virus-Verdict'] = ses.virus
    if ses.via_authentication_results:
        msg['Authentication-Results'] = (
            'amazonses.com; spf=%s (spfCheck: domain of x designates 203.0.113.5) '
            'smtp.mailfrom=x; dkim=%s header.i=@x; dmarc=pass header.from=x'
            % ((ses.spf or 'none').lower(), (ses.dkim or 'none').lower())
        )
    else:
        if ses.spf is not None:
            msg['X-SES-Spf-Verdict'] = ses.spf
        if ses.dkim is not None:
            msg['X-SES-Dkim-Verdict'] = ses.dkim
    for name, value in ses.extra:
        msg[name] = value

    msg['From'] = from_addr
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
    return msg.as_bytes()


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


def write_fixtures() -> None:
    """Regenerate the literal `.eml` files in `fixtures/` (run by hand after changing a builder)."""
    FIXTURES.mkdir(exist_ok=True)
    files = {
        'pdf_attachment.eml': build_email(attachments=[pdf()]),
        'html_body.eml': build_email(text=None, html=HTML_RECEIPT, attachments=[logo()]),
        'gmail_forwarding_confirmation.eml': gmail_forwarding_confirmation(),
        'gmail_auto_forward.eml': gmail_auto_forward(),
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
