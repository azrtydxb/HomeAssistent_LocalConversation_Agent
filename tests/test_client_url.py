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
