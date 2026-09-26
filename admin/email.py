#!python
"""
Send emails using AWS SES.

AWS credentials must be configured.
"""

import os
from typing import TYPE_CHECKING, Annotated

import typer

from .aws import select_aws_profile
from .utils import EnvironmentAnnotation, logger, set_environment

if TYPE_CHECKING:
    # `mypy_boto3_ses` ships in the dev-only `boto3-stubs` group (see `pyproject.toml`), so it
    # isn't installed in production. Keep this import type-checking-only -- the annotation below
    # is on a local variable, which Python never evaluates at runtime -- mirroring the guard in
    # both services' `email_service.py:SesEmailBackend`.
    from mypy_boto3_ses import SESClient

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)


AWS_SES_SENDER = os.getenv('AWS_SES_SENDER', 'no-reply@ziptrigo.com')


def _send_email(
    recipient: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
):
    import boto3

    # Unlike both services' `email_service.py:SesEmailBackend`, which relies on ambient
    # credentials (that's the right call in a deployed environment where the profile is already
    # selected), this is a local dev CLI -- go through `select_aws_profile` so `inv email send`
    # can't silently hit whatever account happens to be ambient.
    region = os.getenv('AWS_REGION', 'us-east-1')
    session = boto3.Session(profile_name=select_aws_profile())
    client: 'SESClient' = session.client('ses', region_name=region)

    if html_body is None:
        html_body = f'<pre>{text_body}</pre>'

    response = client.send_email(
        Source=AWS_SES_SENDER,
        Destination={'ToAddresses': [recipient]},
        Message={
            'Subject': {'Data': subject, 'Charset': 'UTF-8'},
            'Body': {
                'Text': {'Data': text_body, 'Charset': 'UTF-8'},
                'Html': {'Data': html_body, 'Charset': 'UTF-8'},
            },
        },
    )

    message_id = response['MessageId']
    logger.info(f'Email sent, MessageId = {message_id}')


@app.command('send')
def email_send(
    environment: EnvironmentAnnotation,
    to: Annotated[
        str,
        typer.Argument(help='Recipient email address.', show_default=False),
    ],
    subject: Annotated[
        str,
        typer.Option('--subject', '-s', help='Email subject.', show_default=False),
    ],
    text: Annotated[
        str,
        typer.Option('--text', '-t', help='Plain-text body.', show_default=False),
    ],
    html: Annotated[
        str | None,
        typer.Option(
            '--html',
            '-H',
            help='HTML body. If omitted, a simple HTML version of --text is used.',
            show_default=False,
        ),
    ] = None,
):
    """
    Send a test email via Amazon SES.

    Requires AWS credentials. Use the ``aws`` CLI to configure them.
    """
    from botocore.exceptions import ClientError, NoCredentialsError

    set_environment(environment)

    try:
        _send_email(
            recipient=to,
            subject=subject,
            text_body=text,
            html_body=html,
        )
    except (NoCredentialsError, ClientError) as e:
        logger.error(f'AWS credentials not found: {type(e).__name__}: {e}')
        raise typer.Exit(1)


if __name__ == '__main__':
    app()
