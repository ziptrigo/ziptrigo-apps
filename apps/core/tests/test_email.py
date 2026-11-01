"""The SES email backend: what it hands to SES, and that a text-only email's HTML part is escaped."""

from unittest import mock

import pytest

from apps.core.services.email import SesEmailBackend

pytestmark = pytest.mark.unit


def _sent_message(text_body: str, html_body: str | None = None) -> dict:
    with mock.patch('apps.core.services.email.boto3.client') as client_factory:
        SesEmailBackend(sender='no-reply@ziptrigo.com').send_email(
            to='to@example.com', subject='Subject', text_body=text_body, html_body=html_body
        )
    client_factory.return_value.send_email.assert_called_once()
    return client_factory.return_value.send_email.call_args.kwargs['Message']


class TestSesEmailBackend:
    def test_text_only_email_gets_an_escaped_html_part(self):
        message = _sent_message('Hi <b>there</b> & <script>alert(1)</script>\nsecond line')

        assert message['Body']['Html']['Data'] == (
            '<pre>Hi &lt;b&gt;there&lt;/b&gt; &amp; &lt;script&gt;alert(1)&lt;/script&gt;\n'
            'second line</pre>'
        )
        assert message['Body']['Text']['Data'] == (
            'Hi <b>there</b> & <script>alert(1)</script>\nsecond line'
        )

    def test_an_explicit_html_body_is_sent_as_given(self):
        message = _sent_message('plain', '<p>Already <strong>escaped</strong> by its template</p>')

        assert message['Body']['Html']['Data'] == (
            '<p>Already <strong>escaped</strong> by its template</p>'
        )

    def test_links_in_a_text_only_email_survive_escaping(self):
        message = _sent_message('Download: https://app.example.com/t/abc/?a=1&b=2')

        # `&` becomes `&amp;`, which a mail client reads back as `&`: the URL is unchanged.
        assert message['Body']['Html']['Data'] == (
            '<pre>Download: https://app.example.com/t/abc/?a=1&amp;b=2</pre>'
        )
