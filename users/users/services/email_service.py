"""Email service with multiple backend support."""

import logging
from dataclasses import dataclass
from typing import Protocol

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from mypy_boto3_ses import SESClient

logger = logging.getLogger(__name__)


class EmailBackend(Protocol):
    """Protocol for sending a single email."""

    def send_email(
        self,
        to: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
    ):
        """Send an email."""


type EmailBackendClass = type[EmailBackend]


@dataclass(slots=True)
class SesEmailBackend:
    """Email backend using AWS SES."""

    sender: str

    def send_email(
        self,
        to: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
    ):
        if html_body is None:
            html_body = f'<pre>{text_body}</pre>'

        region = getattr(settings, 'AWS_REGION', 'us-east-1')
        client: SESClient = boto3.client('ses', region_name=region)  # type: ignore

        try:
            client.send_email(
                Source=self.sender,
                Destination={'ToAddresses': [to]},
                Message={
                    'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                    'Body': {
                        'Text': {'Data': text_body, 'Charset': 'UTF-8'},
                        'Html': {'Data': html_body, 'Charset': 'UTF-8'},
                    },
                },
            )
        except ClientError as exc:  # pragma: no cover
            raise RuntimeError(f'Error sending email via SES: {exc}') from exc


@dataclass(slots=True)
class ConsoleEmailBackend:
    """Email backend that logs emails to stdout.

    Useful for development and tests.
    """

    def send_email(
        self,
        to: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
    ):
        print('=== Email ===')
        print(f'To: {to}')
        print(f'Subject: {subject}')
        print('Text:')
        print(text_body)
        if html_body:
            print('HTML:')
            print(html_body)


EMAIL_BACKEND_KIND_TO_CLASS: dict[str, EmailBackendClass] = {
    'console': ConsoleEmailBackend,
    'ses': SesEmailBackend,
}


def parse_email_backend_kinds(raw: str) -> list[str]:
    return list({kind.strip().lower() for kind in raw.split(',') if kind.strip()})


def get_email_backend() -> list[EmailBackendClass]:
    """Return the configured email backend classes.

    Selection is controlled via the `EMAIL_BACKENDS` setting (comma-separated kinds).
    """
    raw_backends = getattr(settings, 'EMAIL_BACKENDS', '')
    kinds = parse_email_backend_kinds(raw_backends)

    if not kinds:
        raise RuntimeError('EMAIL_BACKENDS is not configured.')

    unknown = [k for k in kinds if k not in EMAIL_BACKEND_KIND_TO_CLASS]
    if unknown:
        raise RuntimeError(f'Unknown EMAIL_BACKENDS kind(s): {unknown}')

    return [EMAIL_BACKEND_KIND_TO_CLASS[k] for k in kinds]


def build_email_backend(backend_cls: EmailBackendClass) -> EmailBackend:
    if backend_cls is SesEmailBackend:
        return SesEmailBackend(
            sender=getattr(settings, 'AWS_SES_SENDER', 'no-reply@ziptrigo.com'),
        )
    return backend_cls()


def send_email(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    backend_classes: list[EmailBackendClass] | None = None,
) -> tuple[int, int]:
    """Send the same email using all configured backends.

    Returns a tuple of (success_count, failure_count).
    """
    if backend_classes is None:
        backend_classes = get_email_backend()

    successes = 0
    failures = 0

    for backend_cls in backend_classes:
        backend_name = getattr(backend_cls, '__name__', str(backend_cls))
        try:
            backend = build_email_backend(backend_cls)
            backend.send_email(to=to, subject=subject, text_body=text_body, html_body=html_body)
            successes += 1
        except Exception:
            failures += 1
            logger.exception('Email backend %s failed to send email', backend_name)

    return successes, failures
