"""Tests that the integration sets up and exposes a conversation entity."""

from homeassistant.const import CONF_LLM_HASS_API
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.local_llm_conversation.const import (
    CONF_BASE_URL,
    CONF_MODEL,
    DOMAIN,
)


async def test_setup_creates_conversation_entity(hass: HomeAssistant) -> None:
    """Setting up an entry must yield a usable conversation agent."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Qwen",
        data={CONF_BASE_URL: "http://localhost:8000", CONF_MODEL: "qwen3"},
        options={CONF_LLM_HASS_API: [llm.LLM_API_ASSIST]},
    )
    entry.add_to_hass(hass)

    # conversation depends on the homeassistant component for entity exposure.
    assert await async_setup_component(hass, "homeassistant", {})

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    states = [
        state
        for state in hass.states.async_all()
        if state.entity_id.startswith("conversation.")
    ]
    assert states, "no conversation entity was created"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
