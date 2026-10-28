"""`apps.core.services.ai`: what Receipts and To-do share. No test here reaches the network."""

import json
from unittest import mock

import pytest

from apps.core.services import ai


class TestIsConfigured:
    def test_needs_a_key(self, settings):
        settings.OPENAI_API_KEY = ''
        assert ai.is_configured() is False
        settings.OPENAI_API_KEY = 'sk-test'
        assert ai.is_configured() is True


class TestBuildOpenAIClient:
    def test_passes_the_settings_and_the_callers_timeout_and_retries(self, settings):
        settings.OPENAI_API_KEY = 'sk-test'
        settings.OPENAI_BASE_URL = 'https://llm.example/v1'
        with mock.patch('openai.OpenAI') as factory:
            client = ai.build_openai_client(timeout=7.5, max_retries=3)
        factory.assert_called_once_with(
            base_url='https://llm.example/v1', api_key='sk-test', timeout=7.5, max_retries=3
        )
        assert client is factory.return_value

    def test_empty_settings_become_none(self, settings):
        settings.OPENAI_API_KEY = ''
        settings.OPENAI_BASE_URL = ''
        with mock.patch('openai.OpenAI') as factory:
            ai.build_openai_client(timeout=1, max_retries=0)
        kwargs = factory.call_args.kwargs
        assert kwargs['base_url'] is None
        assert kwargs['api_key'] is None


class TestUnwrapJson:
    def test_plain_object(self):
        assert ai.unwrap_json('{"a": 1}') == {'a': 1}

    def test_surrounding_whitespace(self):
        assert ai.unwrap_json('  \n{"a": 1}\n ') == {'a': 1}

    @pytest.mark.parametrize(
        'fence', ['```json\n{"a": 1}\n```', '```\n{"a": 1}\n```', '```JSON {"a": 1}```']
    )
    def test_code_fence(self, fence):
        assert ai.unwrap_json(fence) == {'a': 1}

    def test_one_item_list(self):
        assert ai.unwrap_json('[{"a": 1}]') == {'a': 1}

    def test_object_encoded_as_a_string(self):
        assert ai.unwrap_json(json.dumps(json.dumps({'a': 1}))) == {'a': 1}

    def test_fenced_list_holding_a_string(self):
        assert ai.unwrap_json('```json\n["{\\"a\\": 1}"]\n```') == '{"a": 1}'

    def test_one_layer_of_each_at_most(self):
        # A list of two objects stays a list: the caller's validation refuses it.
        assert ai.unwrap_json('[{"a": 1}, {"b": 2}]') == [{'a': 1}, {'b': 2}]

    def test_a_string_that_is_not_json_is_returned_as_it_is(self):
        assert ai.unwrap_json('"hello"') == 'hello'

    def test_not_json_raises_a_value_error(self):
        with pytest.raises(json.JSONDecodeError):
            ai.unwrap_json('Sure! Here you go.')

    def test_a_scalar_is_returned_unvalidated(self):
        assert ai.unwrap_json('42') == 42
