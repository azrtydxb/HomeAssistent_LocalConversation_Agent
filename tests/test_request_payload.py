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
