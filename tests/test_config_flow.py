"""Tests for the config flow."""

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.local_llm_conversation.const import (
    CONF_BASE_URL,
    CONF_MODEL,
    DOMAIN,
)

MODELS_URL = "http://host:8000/v1/models"


@pytest.fixture(autouse=True)
async def setup_dependencies(hass: HomeAssistant) -> None:
    """conversation depends on the homeassistant component for entity exposure."""
    assert await async_setup_component(hass, "homeassistant", {})


def two_models(mock: AiohttpClientMocker) -> None:
    mock.get(
        MODELS_URL,
        json={"data": [{"id": "qwen3-32b"}, {"id": "llama-3.3-70b"}]},
    )


async def start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_first_step_asks_only_for_the_connection(hass: HomeAssistant) -> None:
    """The model cannot be chosen before the endpoint has been asked."""
    result = await start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert set(result["data_schema"].schema) == {CONF_BASE_URL, CONF_API_KEY}


async def test_models_are_offered_as_choices(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The whole point: pick from what the endpoint actually serves."""
    two_models(aioclient_mock)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: "http://host:8000", CONF_API_KEY: "sk-x"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "model"
    assert result["description_placeholders"] == {"count": "2"}

    selector = result["data_schema"].schema[CONF_MODEL]
    values = [option["value"] for option in selector.config["options"]]
    assert values == ["llama-3.3-70b", "qwen3-32b"]
    assert selector.config["custom_value"] is True


async def test_choosing_a_model_creates_the_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    two_models(aioclient_mock)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: "http://host:8000", CONF_API_KEY: "sk-x"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODEL: "qwen3-32b"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "qwen3-32b"
    assert result["data"] == {
        CONF_BASE_URL: "http://host:8000",
        CONF_API_KEY: "sk-x",
        CONF_MODEL: "qwen3-32b",
    }
    # Without an API selected the agent can talk but not act.
    assert result["options"][CONF_LLM_HASS_API] == ["assist"]


async def test_endpoint_without_a_model_listing_still_proceeds(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Not every server exposes /v1/models; the name can be typed instead."""
    aioclient_mock.get(MODELS_URL, status=404)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: "http://host:8000"}
    )

    assert result["step_id"] == "model"
    assert result["description_placeholders"] == {"count": "0"}
    assert result["data_schema"].schema[CONF_MODEL].config["options"] == []

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODEL: "typed-by-hand"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MODEL] == "typed-by-hand"


async def test_unreachable_endpoint_reports_on_the_url_field(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(MODELS_URL, exc=TimeoutError)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: "http://host:8000"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_BASE_URL: "cannot_connect"}


async def test_rejected_key_reports_on_the_key_field(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A bad key must not be blamed on the URL."""
    aioclient_mock.get(MODELS_URL, status=401)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: "http://host:8000", CONF_API_KEY: "wrong"}
    )

    assert result["errors"] == {CONF_API_KEY: "invalid_auth"}


async def test_the_same_model_cannot_be_added_twice(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    two_models(aioclient_mock)
    for expected in (FlowResultType.CREATE_ENTRY, FlowResultType.ABORT):
        result = await start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: "http://host:8000"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MODEL: "qwen3-32b"}
        )
        assert result["type"] is expected
    assert result["reason"] == "already_configured"


async def test_a_second_model_on_the_same_endpoint_is_allowed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Running a fast and a slow model side by side is a supported setup."""
    two_models(aioclient_mock)
    for model in ("qwen3-32b", "llama-3.3-70b"):
        result = await start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: "http://host:8000"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MODEL: model}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
