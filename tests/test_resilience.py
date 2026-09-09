"""Tests for what happens when the endpoint misbehaves."""

from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import intent
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.local_llm_conversation.client import CannotConnect, InvalidAuth
from custom_components.local_llm_conversation.const import (
    CONF_BASE_URL,
    CONF_MODEL,
    DOMAIN,
    SUBENTRY_TYPE_CONVERSATION,
)

MODELS_URL = "http://localhost:8000/v1/models"


@pytest.fixture(autouse=True)
async def setup_dependencies(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "homeassistant", {})


def make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_BASE_URL: "http://localhost:8000", CONF_API_KEY: "sk-old"},
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                "title": "Voice",
                "unique_id": None,
                "data": {CONF_MODEL: "qwen3"},
            }
        ],
    )
    entry.add_to_hass(hass)
    return entry


async def test_a_dead_endpoint_is_retried_rather_than_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Retrying is right for a server that is merely down; a person cannot help."""
    aioclient_mock.get(MODELS_URL, exc=TimeoutError)
    entry = make_entry(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_a_rejected_key_asks_the_user_instead_of_retrying(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Retrying a bad key forever would never fix it."""
    aioclient_mock.get(MODELS_URL, status=401)
    entry = make_entry(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_reauth_replaces_the_key_and_keeps_the_models(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(MODELS_URL, status=401)
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    (flow,) = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    aioclient_mock.clear_requests()
    aioclient_mock.get(MODELS_URL, json={"data": [{"id": "qwen3"}]})

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_API_KEY: "sk-new"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "sk-new"
    assert len(entry.subentries) == 1


async def test_the_agent_goes_unavailable_when_the_endpoint_dies(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Nothing polls a conversation agent, so it must report this itself."""
    aioclient_mock.get(MODELS_URL, json={"data": [{"id": "qwen3"}]})
    entry = make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("conversation.voice").state != "unavailable"

    def boom(_payload: dict[str, Any]):
        raise CannotConnect("gone")

    with patch.object(entry.runtime_data, "async_stream_chat", side_effect=boom):
        result = await conversation.async_converse(
            hass, "Hello", None, Context(), agent_id="conversation.voice"
        )
    await hass.async_block_till_done()

    # The turn fails cleanly rather than raising at the pipeline.
    assert result.response.response_type is intent.IntentResponseType.ERROR

    assert hass.states.get("conversation.voice").state == "unavailable"


async def test_a_key_revoked_while_running_triggers_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The key was fine at setup; only a person can supply a new one."""
    aioclient_mock.get(MODELS_URL, json={"data": [{"id": "qwen3"}]})
    entry = make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    def rejected(_payload: dict[str, Any]):
        raise InvalidAuth("revoked")

    with patch.object(entry.runtime_data, "async_stream_chat", side_effect=rejected):
        await conversation.async_converse(
            hass, "Hello", None, Context(), agent_id="conversation.voice"
        )
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
    # It cannot work until someone supplies a key, so it must not look healthy.
    assert hass.states.get("conversation.voice").state == "unavailable"
