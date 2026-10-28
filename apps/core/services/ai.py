"""What every product that talks to an OpenAI-compatible model shares (Receipts, To-do).

`core` cannot import a product, so the parts that do not depend on one live here: whether an API
key is set, the `openai.OpenAI` client built from `settings.OPENAI_*` (each product passes its own
timeout and retry count, and picks its own model from its own setting), and `unwrap_json`, which
turns a model's reply text into the JSON value it holds despite the wrappers models put around it.
Each product keeps its own prompt, its pydantic model, its error types and its process-wide client.
"""

import json
import re
from typing import TYPE_CHECKING, Any

from django.conf import settings

if TYPE_CHECKING:
    from openai import OpenAI

#: A reply wrapped in a Markdown code fence (```json ... ```), which some models add.
_CODE_FENCE = re.compile(r'\A```[A-Za-z0-9_-]*[ \t]*\n?(.*?)\n?[ \t]*```\Z', re.DOTALL)


def is_configured() -> bool:
    """Whether an API key is set. A product that also needs a model checks its own setting."""
    return bool(settings.OPENAI_API_KEY)


def build_openai_client(*, timeout: float, max_retries: int) -> 'OpenAI':
    """An `openai.OpenAI` client for `settings.OPENAI_BASE_URL` (unset: the OpenAI API itself) and
    `settings.OPENAI_API_KEY`, with an explicit HTTP timeout and a bounded number of the SDK's own
    quick retries. A missing key is not checked here: the SDK raises `OpenAIError` at the first
    call, which the caller maps like any client-side configuration problem."""
    import openai

    return openai.OpenAI(
        base_url=settings.OPENAI_BASE_URL or None,
        api_key=settings.OPENAI_API_KEY or None,
        timeout=timeout,
        max_retries=max_retries,
    )


def _unwrap(payload: Any) -> Any:
    """Undo the wrappers models put around the object despite the strict schema: a list holding
    exactly one object (`qwen/qwen3.6-plus` replies `[{...}]`) and the object JSON-encoded as a
    string -- one layer of each, at most. Anything else is returned unchanged, so it still fails
    validation."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return payload
    if isinstance(payload, list) and len(payload) == 1:
        payload = payload[0]
    return payload


def unwrap_json(raw: str) -> Any:
    """The JSON value in a model's reply `raw`: tolerates one code fence, a one-item list and a
    JSON string around the object (an OpenAI-compatible endpoint does not necessarily enforce
    `response_format`'s strict schema). Raises `json.JSONDecodeError` (a `ValueError`) when the
    text is not JSON. What comes back is not validated: it need not be an object; the caller
    checks and validates it (pydantic)."""
    text = raw.strip()
    fenced = _CODE_FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    return _unwrap(json.loads(text))
