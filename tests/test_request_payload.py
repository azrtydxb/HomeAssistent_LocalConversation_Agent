"""Tests for what the integration actually sends to the endpoint."""

from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.local_llm_conversation.const import (
    CONF_ADVANCED,
    CONF_BASE_URL,
    CONF_MODEL,
    CONF_THINKING,
    DOMAIN,
    SUBENTRY_TYPE_CONVERSATION,
)


@pytest.fixture(autouse=True)
async def setup_dependencies(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "homeassistant", {})


@pytest.fixture(autouse=True)
def endpoint_answers(aioclient_mock):
    """Setup now reaches the endpoint before claiming the agents work."""
    for host in ("http://localhost:8000", "http://192.168.1.10:4000/v1"):
        aioclient_mock.get(f"{host.removesuffix('/v1')}/v1/models", json={"data": []})
    return aioclient_mock


async def reply(_payload: dict[str, Any]):
    """A minimal well-formed stream: one content delta, then done."""
    yield {"delta": {"role": "assistant", "content": "Blue."}}
    yield {"delta": {}, "finish_reason": "stop"}


async def ask(hass: HomeAssistant, advanced: dict[str, Any]) -> dict[str, Any]:
    """Run one turn and return the payload that was sent."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_BASE_URL: "http://localhost:8000"},
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                "title": "Voice",
                "unique_id": None,
                "data": {CONF_MODEL: "qwen3", CONF_ADVANCED: advanced},
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    sent: list[dict[str, Any]] = []

    def capture(payload: dict[str, Any]):
        sent.append(payload)
        return reply(payload)

    with patch.object(entry.runtime_data, "async_stream_chat", side_effect=capture):
        result = await conversation.async_converse(
            hass,
            "What colour is the sky?",
            None,
            Context(),
            agent_id="conversation.voice",
        )

    assert result.response.speech["plain"]["speech"] == "Blue."
    assert len(sent) == 1
    return sent[0]


async def test_thinking_is_switched_off_by_default(hass: HomeAssistant) -> None:
    """Reasoning costs seconds and most of the token budget on every turn."""
    payload = await ask(hass, {})
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


async def test_thinking_can_be_switched_on(hass: HomeAssistant) -> None:
    """Enabled, nothing is sent and the server's own default applies."""
    payload = await ask(hass, {CONF_THINKING: True})
    assert "chat_template_kwargs" not in payload


async def test_the_model_and_prompt_are_sent(hass: HomeAssistant) -> None:
    payload = await ask(hass, {})
    assert payload["model"] == "qwen3"
    assert payload["messages"][0]["role"] == "system"
    assert "Jarvis" in payload["messages"][0]["content"]
    assert payload["messages"][-1] == {
        "role": "user",
        "content": "What colour is the sky?",
    }


async def test_the_request_prefix_is_byte_stable_across_turns(
    hass: HomeAssistant,
) -> None:
    """Prefix caching is worth roughly 3x on time to first token.

    Measured against vLLM with a 9k-token prompt: a stable prefix answers in
    0.8s, a prefix that changes every turn in 2.7s. Anything that makes the
    model list, the tool definitions or the system prompt differ between turns
    throws that away, so the leading part of the request must serialise
    identically each time.
    """
    import json

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_BASE_URL: "http://localhost:8000"},
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                "title": "Voice",
                "unique_id": None,
                "data": {
                    CONF_MODEL: "qwen3",
                    "llm_hass_api": ["assist"],
                    CONF_ADVANCED: {},
                },
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    sent: list[dict[str, Any]] = []

    def capture(payload: dict[str, Any]):
        sent.append(payload)
        return reply(payload)

    with patch.object(entry.runtime_data, "async_stream_chat", side_effect=capture):
        for _ in range(3):
            await conversation.async_converse(
                hass, "Hello", None, Context(), agent_id="conversation.voice"
            )

    assert len(sent) == 3
    first, *rest = sent
    for payload in rest:
        assert payload["model"] == first["model"]
        # Tool order and serialisation must not wander between turns.
        assert json.dumps(payload.get("tools")) == json.dumps(first.get("tools"))
        # The system prompt is the bulk of the prefix.
        assert payload["messages"][0] == first["messages"][0]
