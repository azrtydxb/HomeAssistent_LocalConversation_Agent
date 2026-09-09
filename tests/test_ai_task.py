"""Tests for generating data with the model."""

from typing import Any
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.components import ai_task
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.local_llm_conversation.const import (
    CONF_BASE_URL,
    CONF_MODEL,
    DOMAIN,
    SUBENTRY_TYPE_AI_TASK,
)

MODELS_URL = "http://localhost:8000/v1/models"


@pytest.fixture(autouse=True)
async def setup_dependencies(hass: HomeAssistant, aioclient_mock) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "ai_task", {})
    aioclient_mock.get(MODELS_URL, json={"data": [{"id": "qwen3"}]})


def answering(text: str):
    async def stream(_payload: dict[str, Any]):
        yield {"delta": {"role": "assistant", "content": text}}
        yield {"delta": {}, "finish_reason": "stop"}

    return stream


async def setup_task_entity(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_BASE_URL: "http://localhost:8000"},
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_AI_TASK,
                "title": "Tasks",
                "unique_id": None,
                "data": {CONF_MODEL: "qwen3"},
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_a_task_entity_is_created(hass: HomeAssistant) -> None:
    await setup_task_entity(hass)
    assert hass.states.get("ai_task.tasks") is not None


async def test_plain_text_is_returned_as_written(hass: HomeAssistant) -> None:
    entry = await setup_task_entity(hass)
    with patch.object(
        entry.runtime_data, "async_stream_chat", side_effect=answering("Milk and eggs.")
    ):
        result = await ai_task.async_generate_data(
            hass,
            task_name="shopping",
            entity_id="ai_task.tasks",
            instructions="What is missing?",
        )
    assert result.data == "Milk and eggs."


async def test_a_structure_is_returned_as_parsed_data(hass: HomeAssistant) -> None:
    """The point of AI Task: automations get data, not prose."""
    entry = await setup_task_entity(hass)
    sent: list[dict[str, Any]] = []

    def capture(payload: dict[str, Any]):
        sent.append(payload)
        return answering('{"colour": "blue", "confidence": 0.9}')(payload)

    with patch.object(entry.runtime_data, "async_stream_chat", side_effect=capture):
        result = await ai_task.async_generate_data(
            hass,
            task_name="sky",
            entity_id="ai_task.tasks",
            instructions="What colour is the sky?",
            structure=vol.Schema(
                {
                    vol.Required("colour"): str,
                    vol.Required("confidence"): vol.Coerce(float),
                }
            ),
        )

    assert result.data == {"colour": "blue", "confidence": 0.9}
    # The schema must reach the endpoint, or the model is free to answer in prose.
    fmt = sent[0]["response_format"]
    assert fmt["type"] == "json_schema"
    assert set(fmt["json_schema"]["schema"]["properties"]) == {"colour", "confidence"}


async def test_prose_where_a_structure_was_asked_for_fails_clearly(
    hass: HomeAssistant,
) -> None:
    """Endpoints that ignore response_format answer in prose; say so plainly."""
    entry = await setup_task_entity(hass)
    with (
        patch.object(
            entry.runtime_data,
            "async_stream_chat",
            side_effect=answering("The sky is blue, because of Rayleigh scattering."),
        ),
        pytest.raises(HomeAssistantError, match="requested structure"),
    ):
        await ai_task.async_generate_data(
            hass,
            task_name="sky",
            entity_id="ai_task.tasks",
            instructions="What colour is the sky?",
            structure=vol.Schema({vol.Required("colour"): str}),
        )


async def test_conversation_and_tasks_can_share_one_provider(
    hass: HomeAssistant,
) -> None:
    """One endpoint, a chat agent and a task worker, each its own model."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_BASE_URL: "http://localhost:8000"},
        subentries_data=[
            {
                "subentry_type": "conversation",
                "title": "Voice",
                "unique_id": None,
                "data": {CONF_MODEL: "qwen3"},
            },
            {
                "subentry_type": SUBENTRY_TYPE_AI_TASK,
                "title": "Tasks",
                "unique_id": None,
                "data": {CONF_MODEL: "llama"},
            },
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("conversation.voice") is not None
    assert hass.states.get("ai_task.tasks") is not None
