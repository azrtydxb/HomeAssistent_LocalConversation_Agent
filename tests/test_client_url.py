"""Tests for base URL normalization."""

import pytest

from custom_components.local_llm_conversation.client import normalize_base_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Bare host: the common vLLM / SGLang case.
        ("http://192.168.1.10:8000", "http://192.168.1.10:8000/v1"),
        # Trailing slash must not produce a double slash.
        ("http://host:8000/", "http://host:8000/v1"),
        # Already an API root: left alone.
        ("http://host:8000/v1", "http://host:8000/v1"),
        ("http://host:8000/v1/", "http://host:8000/v1"),
        # Full endpoint pasted from docs or curl.
        ("http://host:8000/v1/chat/completions", "http://host:8000/v1"),
        # Proxy served under a path prefix keeps the prefix.
        ("https://gw.example/openai/v1", "https://gw.example/openai/v1"),
        ("https://gw.example/openai", "https://gw.example/openai/v1"),
        # Surrounding whitespace from a copy-paste.
        ("  http://host:8000  ", "http://host:8000/v1"),
    ],
)
def test_normalize_base_url(raw: str, expected: str) -> None:
    assert normalize_base_url(raw) == expected


async def test_context_window_is_read_when_the_endpoint_announces_it(
    hass, aioclient_mock
) -> None:
    """vLLM and SGLang report max_model_len; it bounds how long a reply may be."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from custom_components.local_llm_conversation.client import ChatCompletionsClient

    aioclient_mock.get(
        "http://host:8000/v1/models",
        json={
            "data": [
                {"id": "qwen", "max_model_len": 262144},
                {"id": "gpt-5", "context_length": 400000},
                {"id": "mystery"},
                {"id": "broken", "max_model_len": 0},
            ]
        },
    )
    client = ChatCompletionsClient(
        async_get_clientsession(hass), "http://host:8000", None, 30
    )
    models = {
        model.id: model.context_length for model in await client.async_list_models()
    }

    assert models == {
        "qwen": 262144,
        "gpt-5": 400000,
        # Not every server announces one, and a nonsense value is not a window.
        "mystery": None,
        "broken": None,
    }
