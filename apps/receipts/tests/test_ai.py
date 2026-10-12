import json
import re
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import openai
import pytest

from ..services import ai
from ..services.ai import (
    ExtractedReceipt,
    ExtractionRequest,
    ImagePart,
    InvalidAIResponseError,
    PermanentAIError,
    TransientAIError,
    mask_payment_method,
    parse_response,
)

pytestmark = [pytest.mark.unit]


class TestMaskPaymentMethod:
    @pytest.mark.parametrize(
        ('raw', 'masked'),
        [
            ('4111111111111111', '••1111'),
            ('Visa 4111 1111 1111 1111', 'Visa ••1111'),
            ('Mastercard 5500-0000-0000-0004', 'Mastercard ••0004'),
            ('Visa ••4242', 'Visa ••4242'),
            ('ending in 4242', 'ending in 4242'),
            ('IBAN DE89370400440532013000', 'IBAN DE••3000'),
            ('Cash', 'Cash'),
            ('12345', '••2345'),
            ('Visa 4111.1111.1111.1111', 'Visa ••1111'),
            ('Visa 4111/1111/1111/1111', 'Visa ••1111'),
            ('Visa 4111  1111  1111  1111', 'Visa ••1111'),
            ('Visa 4111 - 1111 - 1111 - 1111', 'Visa ••1111'),
            ('Visa 4111 1111.1111-1111/', 'Visa ••1111/'),
            ('Visa 4111\t1111\n1111 1111', 'Visa ••1111'),
            ('Amex 3782.822463.10005', 'Amex ••0005'),
            ('Card 4111 1111 1111 1111 exp 12/26', 'Card ••1111 exp 12/26'),
            ('Visa ••4242 and Mastercard ••1111', 'Visa ••4242 and Mastercard ••1111'),
        ],
    )
    def test_masking(self, raw, masked):
        assert mask_payment_method(raw) == masked

    def test_a_long_digit_run_never_survives(self):
        for raw in (
            'Card 1234567890123456',
            'acct 98765 43210 1111 ref',
            'Card 4111.1111.1111.1111',
            'Card 4111/1111/1111/1111',
            'Card 4111  1111  1111  1111',
            'Card 4111 . 1111 / 1111 - 1111',
        ):
            masked = mask_payment_method(raw)
            assert not re.search(r'\d{5}', masked)


class TestExtractedReceipt:
    def test_the_stored_result_is_masked_too(self):
        extracted = ExtractedReceipt.model_validate(
            {'is_receipt': True, 'receipt_count': 1, 'payment_method': 'Visa 4111.1111.1111.1111'}
        )

        assert extracted.payment_method == 'Visa ••1111'
        assert extracted.model_dump(mode='json')['payment_method'] == 'Visa ••1111'

    def test_validates_a_full_response(self):
        extracted = ExtractedReceipt.model_validate(
            {
                'receipt_count': 1,
                'is_receipt': True,
                'vendor': ' Staples ',
                'amount': '42.1',
                'currency': 'usd',
                'receipt_date': '2026-10-09',
                'document_type': 'Invoice',
                'document_number': 'R-1',
                'payment_method': 'Visa 4242 4242 4242 4242',
                'category': 'Travel',
                'confidence': 0.9,
            }
        )

        assert extracted.vendor == 'Staples'
        assert extracted.amount == Decimal('42.10')
        assert extracted.currency == 'USD'
        assert extracted.receipt_date == date(2026, 10, 9)
        assert extracted.document_type == 'invoice'
        assert extracted.payment_method == 'Visa ••4242'

    def test_unparseable_values_become_missing_instead_of_failing(self):
        extracted = ExtractedReceipt.model_validate(
            {
                'amount': 'about forty',
                'receipt_date': 'last Tuesday',
                'currency': 'dollars',
                'document_type': 'bill',
                'vendor': '   ',
            }
        )

        assert extracted.amount is None
        assert extracted.receipt_date is None
        assert extracted.currency is None
        assert extracted.document_type is None
        assert extracted.vendor is None

    @pytest.mark.parametrize('amount', ['-5', '1e20', 'NaN', 'Infinity', True])
    def test_implausible_amounts_are_missing(self, amount):
        assert ExtractedReceipt.model_validate({'amount': amount}).amount is None

    def test_amount_with_thousands_separator(self):
        assert ExtractedReceipt.model_validate({'amount': '1,234.5'}).amount == Decimal('1234.50')

    def test_confidence_is_clamped(self):
        assert ExtractedReceipt.model_validate({'confidence': 7}).confidence == 1.0
        assert ExtractedReceipt.model_validate({'confidence': -1}).confidence == 0.0
        assert ExtractedReceipt.model_validate({'confidence': 'high'}).confidence == 0.0

    def test_text_fields_are_capped_to_their_columns(self):
        extracted = ExtractedReceipt.model_validate(
            {'vendor': 'v' * 1000, 'document_number': 'n' * 1000, 'payment_method': 'p' * 1000}
        )

        assert len(extracted.vendor or '') == 255
        assert len(extracted.document_number or '') == 100
        assert len(extracted.payment_method or '') == 100

    def test_unknown_keys_are_ignored(self):
        assert ExtractedReceipt.model_validate({'vendor': 'A', 'surprise': 1}).vendor == 'A'


class TestParseResponse:
    def test_parses_json(self):
        assert parse_response(json.dumps({'vendor': 'A', 'confidence': 0.5})).vendor == 'A'

    @pytest.mark.parametrize('raw', [None, '', '   ', 'not json', '[1, 2]', '"text"'])
    def test_anything_else_is_an_invalid_response(self, raw):
        with pytest.raises(InvalidAIResponseError):
            parse_response(raw)

    def test_an_invalid_response_counts_as_transient(self):
        assert issubclass(InvalidAIResponseError, TransientAIError)

    def test_a_schema_mismatch_is_invalid(self):
        with pytest.raises(InvalidAIResponseError):
            parse_response(json.dumps({'receipt_count': 'many'}))


class TestRequest:
    def test_messages_carry_categories_text_and_images_as_data_uris(self):
        request = ExtractionRequest(
            categories=['Travel', 'Other'],
            images=[ImagePart('image/jpeg', b'\xff\xd8abc')],
            text='Total 42.10',
        )

        messages = ai.build_messages(request)

        assert messages[0]['role'] == 'system'
        content = messages[1]['content']
        assert '- Travel' in content[0]['text'] and '- Other' in content[0]['text']
        assert 'Total 42.10' in content[1]['text']
        assert content[2]['image_url']['url'].startswith('data:image/jpeg;base64,')

    def test_the_schema_is_strict_and_requires_every_property(self):
        schema = ai.RESPONSE_SCHEMA

        assert schema['additionalProperties'] is False
        assert set(schema['required']) == set(schema['properties'])
        assert set(schema['properties']) == set(ExtractedReceipt.model_fields)


def _completion(content: str | None, refusal: str | None = None):
    message = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _status_error(status: int) -> openai.APIStatusError:
    import httpx

    response = httpx.Response(status, request=httpx.Request('POST', 'https://x.test/v1'))
    return openai.APIStatusError('boom', response=response, body=None)


class FakeCompletions:
    def __init__(self, result):
        self.result = result
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def openai_client(monkeypatch):
    """An `OpenAIReceiptClient` whose SDK client is replaced by a fake `completions`."""

    def build(result):
        client = ai.OpenAIReceiptClient()
        completions = FakeCompletions(result)
        client._client = SimpleNamespace(  # ty: ignore[invalid-assignment]
            chat=SimpleNamespace(completions=completions)
        )
        return client, completions

    return build


REQUEST = ExtractionRequest(categories=['Travel'], text='hello')


class TestOpenAIClient:
    @pytest.fixture(autouse=True)
    def _configured(self, settings):
        settings.OPENAI_API_KEY = 'sk-test'
        settings.OPENAI_RECEIPTS_MODEL = 'gpt-test'

    def test_builds_the_sdk_client_with_timeout_and_bounded_retries(self, settings):
        settings.OPENAI_BASE_URL = 'https://llm.example.test/v1'
        settings.RECEIPTS_AI_TIMEOUT_SECONDS = 12.5
        settings.RECEIPTS_AI_MAX_RETRIES = 1

        client = ai.OpenAIReceiptClient()

        assert str(client._client.base_url).startswith('https://llm.example.test/v1')
        assert client._client.timeout == 12.5
        assert client._client.max_retries == 1

    def test_a_blank_base_url_means_the_openai_api(self, settings):
        settings.OPENAI_BASE_URL = ''

        assert ai.OpenAIReceiptClient()._client.base_url.host == 'api.openai.com'

    def test_success_sends_a_json_schema_request_and_validates_the_reply(self, openai_client):
        reply = json.dumps({'vendor': 'Staples', 'amount': 42.1, 'confidence': 0.8})
        client, completions = openai_client(_completion(reply))

        extracted = client.extract(REQUEST)

        assert extracted.vendor == 'Staples'
        assert completions.kwargs['model'] == 'gpt-test'
        response_format = completions.kwargs['response_format']
        assert response_format['type'] == 'json_schema'
        assert response_format['json_schema']['strict'] is True
        assert response_format['json_schema']['schema'] == ai.RESPONSE_SCHEMA

    @pytest.mark.parametrize('status', [408, 409, 429, 500, 502, 503])
    def test_transient_http_statuses(self, openai_client, status):
        client, _ = openai_client(_status_error(status))

        with pytest.raises(TransientAIError):
            client.extract(REQUEST)

    @pytest.mark.parametrize('status', [400, 401, 403, 404, 422])
    def test_permanent_http_statuses(self, openai_client, status):
        client, _ = openai_client(_status_error(status))

        with pytest.raises(PermanentAIError):
            client.extract(REQUEST)

    def test_timeouts_and_connection_errors_are_transient(self, openai_client):
        import httpx

        request = httpx.Request('POST', 'https://x.test')
        for error in (
            openai.APITimeoutError(request=request),
            openai.APIConnectionError(request=request),
        ):
            client, _ = openai_client(error)
            with pytest.raises(TransientAIError):
                client.extract(REQUEST)

    def test_a_refusal_is_an_invalid_response(self, openai_client):
        client, _ = openai_client(_completion(None, refusal="I can't help with that"))

        with pytest.raises(InvalidAIResponseError):
            client.extract(REQUEST)

    def test_an_empty_reply_is_an_invalid_response(self, openai_client):
        client, _ = openai_client(SimpleNamespace(choices=[]))

        with pytest.raises(InvalidAIResponseError):
            client.extract(REQUEST)


class TestGetClient:
    def test_unconfigured_is_a_permanent_error(self, settings, monkeypatch):
        monkeypatch.setattr(ai, '_client', None)
        settings.OPENAI_API_KEY = ''
        settings.OPENAI_RECEIPTS_MODEL = ''

        with pytest.raises(PermanentAIError):
            ai.get_client()

    def test_configured_builds_and_caches_the_real_client(self, settings, monkeypatch):
        monkeypatch.setattr(ai, '_client', None)
        settings.OPENAI_API_KEY = 'sk-test'
        settings.OPENAI_RECEIPTS_MODEL = 'm'

        first = ai.get_client()

        assert isinstance(first, ai.OpenAIReceiptClient)
        assert ai.get_client() is first
        ai.set_client(None)
