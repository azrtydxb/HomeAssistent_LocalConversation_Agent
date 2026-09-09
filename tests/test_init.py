"""Tests for provider setup, entities, and the v1 to v2 migration."""

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.local_llm_conversation.const import (
    CONF_ADVANCED,
    CONF_ASSISTANT_NAME,
    CONF_BASE_URL,
    CONF_MODEL,
    CONF_PROMPT,
    CONF_TEMPERATURE,
    DOMAIN,
    SUBENTRY_TYPE_CONVERSATION,
)


@pytest.fixture(autouse=True)
async def setup_dependencies(hass: HomeAssistant) -> None:
    """conversation depends on the homeassistant component for entity exposure."""
    assert await async_setup_component(hass, "homeassistant", {})


def conversation_entities(hass: HomeAssistant) -> list[str]:
    return [
        state.entity_id
        for state in hass.states.async_all()
        if state.entity_id.startswith("conversation.")
        and state.entity_id != "conversation.home_assistant"
    ]


async def test_each_model_gets_its_own_agent(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="http://localhost:8000",
        data={CONF_BASE_URL: "http://localhost:8000"},
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                "title": "Voice",
                "unique_id": None,
                "data": {CONF_MODEL: "qwen3", CONF_LLM_HASS_API: ["assist"]},
            },
            {
                "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                "title": "Study",
                "unique_id": None,
                "data": {CONF_MODEL: "llama", CONF_LLM_HASS_API: ["assist"]},
            },
        ],
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(conversation_entities(hass)) == 2

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_migration_keeps_an_existing_setup_working(hass: HomeAssistant) -> None:
    """A v1 entry held the endpoint and one model together.

    Losing this migration would silently drop a working agent on upgrade.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        title="qwen3-6-35b-a3b",
        data={
            CONF_BASE_URL: "http://192.168.1.10:4000/v1",
            CONF_API_KEY: "sk-x",
            CONF_MODEL: "qwen3-6-35b-a3b",
        },
        options={
            CONF_LLM_HASS_API: ["assist"],
            CONF_TEMPERATURE: 0.4,
            CONF_PROMPT: "be terse",
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    # The provider keeps only the endpoint.
    assert CONF_MODEL not in entry.data
    assert entry.data[CONF_BASE_URL] == "http://192.168.1.10:4000/v1"

    subentry = next(iter(entry.subentries.values()))
    assert subentry.subentry_type == SUBENTRY_TYPE_CONVERSATION
    assert subentry.title == "qwen3-6-35b-a3b"
    assert subentry.data[CONF_MODEL] == "qwen3-6-35b-a3b"
    assert subentry.data[CONF_LLM_HASS_API] == ["assist"]
    # Tuning that was set stays set, now inside the advanced section.
    assert subentry.data[CONF_ADVANCED][CONF_TEMPERATURE] == 0.4
    assert subentry.data[CONF_ADVANCED][CONF_PROMPT] == "be terse"

    assert len(conversation_entities(hass)) == 1


async def test_a_newer_entry_is_not_downgraded(hass: HomeAssistant) -> None:
    """Rolling back must fail loudly rather than mangle the entry.

    Home Assistant enforces this itself; the test pins the behaviour we rely on
    instead of duplicating the check in the migration.
    """
    entry = MockConfigEntry(
        domain=DOMAIN, version=3, data={CONF_BASE_URL: "http://localhost:8000"}
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)


async def test_the_soul_carries_the_assistant_name(hass: HomeAssistant) -> None:
    """The point of the name field: the agent answers to what you call it."""
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
                    CONF_ASSISTANT_NAME: "Alfred",
                    CONF_ADVANCED: {CONF_PROMPT: "You are {name}, at your service."},
                },
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from custom_components.local_llm_conversation.conversation import (
        LocalLLMConversationEntity,
    )

    agent = LocalLLMConversationEntity(entry, next(iter(entry.subentries.values())))
    assert agent._soul == "You are Alfred, at your service."


async def test_the_default_soul_is_used_when_none_is_set(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_BASE_URL: "http://localhost:8000"},
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
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from custom_components.local_llm_conversation.conversation import (
        LocalLLMConversationEntity,
    )

    agent = LocalLLMConversationEntity(entry, next(iter(entry.subentries.values())))
    assert agent._soul.startswith("You are Jarvis, the digital butler")
    assert "{name}" not in agent._soul
