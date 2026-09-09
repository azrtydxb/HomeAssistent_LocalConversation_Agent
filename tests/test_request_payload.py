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


async def ask_with_image(hass: HomeAssistant, tmp_path, vision: bool) -> dict[str, Any]:
    """Run one turn carrying a snapshot and return the payload that was sent."""
    from homeassistant.components.conversation import (
        Attachment,
        ConversationInput,
        UserContent,
        async_get_chat_log,
    )
    from homeassistant.helpers import chat_session

    from custom_components.local_llm_conversation.const import CONF_VISION

    png = tmp_path / "door.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_BASE_URL: "http://localhost:8000"},
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                "title": f"Voice {vision}",
                "unique_id": None,
                "data": {
                    CONF_MODEL: "qwen3",
                    CONF_ADVANCED: {CONF_VISION: vision},
                },
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    agent = hass.data["entity_components"]["conversation"].get_entity(
        f"conversation.voice_{str(vision).lower()}"
    )
    user_input = ConversationInput(
        text="Who is at the door?",
        context=Context(),
        conversation_id=None,
        device_id=None,
        satellite_id=None,
        language="en",
        agent_id=agent.entity_id,
    )

    sent: list[dict[str, Any]] = []

    def capture(payload: dict[str, Any]):
        sent.append(payload)
        return reply(payload)

    with (
        patch.object(entry.runtime_data, "async_stream_chat", side_effect=capture),
        chat_session.async_get_chat_session(hass) as session,
        async_get_chat_log(hass, session) as chat_log,
    ):
        chat_log.async_add_user_content(
            UserContent(
                content="Who is at the door?",
                attachments=[
                    Attachment(
                        media_content_id="media://door",
                        mime_type="image/png",
                        path=png,
                    )
                ],
            )
        )
        await agent._async_handle_message(user_input, chat_log)

    return sent[0]


def carries_an_image(payload: dict[str, Any]) -> bool:
    content = payload["messages"][-1]["content"]
    return isinstance(content, list) and any(
        part["type"] == "image_url" for part in content
    )


async def test_a_snapshot_reaches_a_model_that_reads_images(
    hass: HomeAssistant, tmp_path
) -> None:
    assert carries_an_image(await ask_with_image(hass, tmp_path, vision=True))


async def test_a_snapshot_is_withheld_from_a_model_that_ignores_images(
    hass: HomeAssistant, tmp_path
) -> None:
    """Sending it would cost prompt tokens for something never looked at."""
    assert not carries_an_image(await ask_with_image(hass, tmp_path, vision=False))


async def payload_for_tool_mode(hass: HomeAssistant, mode: str) -> dict[str, Any]:
    """Run one turn with the given tool mode and return what was sent."""
    from custom_components.local_llm_conversation.const import CONF_TOOL_MODE

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_BASE_URL: "http://localhost:8000"},
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                "title": f"Voice {mode}",
                "unique_id": None,
                "data": {
                    CONF_MODEL: "qwen3",
                    "llm_hass_api": ["assist"],
                    CONF_ADVANCED: {CONF_TOOL_MODE: mode},
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
        await conversation.async_converse(
            hass, "Hello", None, Context(), agent_id=f"conversation.voice_{mode}"
        )
    return sent[0]


async def test_native_mode_sends_tools_in_the_request(hass: HomeAssistant) -> None:
    payload = await payload_for_tool_mode(hass, "native")
    assert payload["tools"]
    assert (
        "tool"
        not in payload["messages"][0]["content"].lower().split("available")[0][-40:]
    )


async def test_prompted_mode_describes_tools_in_the_prompt_instead(
    hass: HomeAssistant,
) -> None:
    """For stacks with no tool calling, the request must carry no tools field."""
    payload = await payload_for_tool_mode(hass, "prompted")
    assert "tools" not in payload
    system = payload["messages"][0]["content"]
    assert "Available tools:" in system
    assert "HassTurnOn" in system


async def test_no_tools_mode_offers_none_at_all(hass: HomeAssistant) -> None:
    payload = await payload_for_tool_mode(hass, "none")
    assert "tools" not in payload
    assert "Available tools:" not in payload["messages"][0]["content"]
