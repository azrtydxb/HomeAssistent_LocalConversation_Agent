"""Tests for the diagnostics download."""

import pytest
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.local_llm_conversation.const import (
    CONF_ADVANCED,
    CONF_BASE_URL,
    CONF_MODEL,
    CONF_PROMPT,
    DOMAIN,
    SUBENTRY_TYPE_CONVERSATION,
)
from custom_components.local_llm_conversation.diagnostics import (
    async_get_config_entry_diagnostics,
)

MODELS_URL = "http://localhost:8000/v1/models"
SECRET = "sk-do-not-publish-this"


@pytest.fixture(autouse=True)
async def setup_dependencies(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "homeassistant", {})


async def diagnostics(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(MODELS_URL, json={"data": [{"id": "qwen3"}]})
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_BASE_URL: "http://localhost:8000", CONF_API_KEY: SECRET},
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                "title": "Voice",
                "unique_id": None,
                "data": {
                    CONF_MODEL: "qwen3",
                    CONF_ADVANCED: {
                        CONF_PROMPT: "You are Jarvis. Pascal takes his tea black.",
                        "temperature": 0.4,
                    },
                },
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return await async_get_config_entry_diagnostics(hass, entry)


async def test_the_api_key_never_appears(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Diagnostics get pasted into public bug reports."""
    report = await diagnostics(hass, aioclient_mock)
    assert SECRET not in str(report)
    assert report["entry"]["has_api_key"] is True


async def test_the_persona_is_reported_by_size_not_content(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A household may have written something personal into the soul."""
    report = await diagnostics(hass, aioclient_mock)
    advanced = report["subentries"][0]["data"][CONF_ADVANCED]

    assert "Pascal takes his tea black" not in str(report)
    assert CONF_PROMPT not in advanced
    assert advanced["prompt_length"] == 43
    # Settings that matter for a bug report survive.
    assert advanced["temperature"] == 0.4


async def test_what_a_bug_report_needs_is_present(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    report = await diagnostics(hass, aioclient_mock)
    assert report["entry"]["version"] == 2
    assert report["entry"]["data"][CONF_BASE_URL] == "http://localhost:8000"
    assert report["models_offered"] == ["qwen3"]
    assert report["subentries"][0]["data"][CONF_MODEL] == "qwen3"


async def test_an_unreachable_endpoint_is_reported_not_raised(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Diagnostics are most wanted exactly when the endpoint is broken."""
    aioclient_mock.get(MODELS_URL, json={"data": []})
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_BASE_URL: "http://localhost:8000"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    aioclient_mock.clear_requests()
    aioclient_mock.get(MODELS_URL, exc=TimeoutError)
    report = await async_get_config_entry_diagnostics(hass, entry)
    assert "unreachable" in report["models_offered"]
