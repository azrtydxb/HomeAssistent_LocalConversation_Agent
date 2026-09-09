"""Tests for drafting automations."""

import pytest
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component

from custom_components.local_llm_conversation.automation_api import (
    ProposeAutomationTool,
)


@pytest.fixture(autouse=True)
async def setup_dependencies(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "automation", {})


def call(**args):
    return llm.ToolInput(tool_name="ProposeAutomation", tool_args=args)


def context() -> llm.LLMContext:
    return llm.LLMContext(
        platform="local_llm_conversation",
        context=None,
        language="en",
        assistant="conversation",
        device_id=None,
    )


def only_notification(hass: HomeAssistant) -> str:
    """Return the single raised notification's message."""
    notifications = hass.data.get(persistent_notification.DOMAIN, {})
    assert len(notifications) == 1, (
        f"expected one notification, got {len(notifications)}"
    )
    entry = next(iter(notifications.values()))
    return entry["message"] if isinstance(entry, dict) else entry.message


VALID = {
    "alias": "Porch light at sunset",
    "triggers": [{"trigger": "sun", "event": "sunset"}],
    "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}],
}


async def test_a_draft_is_not_created(hass: HomeAssistant) -> None:
    """An automation runs unattended, so nothing appears without a person."""
    before = set(hass.states.async_entity_ids("automation"))

    result = await ProposeAutomationTool().async_call(hass, call(**VALID), context())

    assert result["created"] is False
    assert result["proposed"] == "Porch light at sunset"
    assert set(hass.states.async_entity_ids("automation")) == before


async def test_the_draft_reaches_a_person(hass: HomeAssistant) -> None:
    await ProposeAutomationTool().async_call(hass, call(**VALID), context())
    await hass.async_block_till_done()

    body = only_notification(hass)
    assert "Porch light at sunset" in body
    assert "sunset" in body
    assert "Nothing has been" in body


async def test_the_model_is_told_not_to_claim_it_is_running(
    hass: HomeAssistant,
) -> None:
    """Otherwise it reports success and the household believes it is set up."""
    result = await ProposeAutomationTool().async_call(hass, call(**VALID), context())
    assert "not running" in result["note"]
    assert "suggested, not set up" in result["note"]


async def test_an_invalid_draft_goes_back_to_the_model(hass: HomeAssistant) -> None:
    """A broken draft is a draft, not something to put in front of a person."""
    result = await ProposeAutomationTool().async_call(
        hass,
        call(
            alias="Nonsense",
            triggers=[{"trigger": "not_a_real_trigger"}],
            actions=[{"action": "light.turn_on"}],
        ),
        context(),
    )

    assert "not valid" in result["error"]
    assert "proposed" not in result
    assert not hass.data.get(persistent_notification.DOMAIN, {})


async def test_conditions_are_carried_through(hass: HomeAssistant) -> None:
    result = await ProposeAutomationTool().async_call(
        hass,
        call(
            **VALID,
            conditions=[
                {"condition": "state", "entity_id": "person.someone", "state": "home"}
            ],
        ),
        context(),
    )
    await hass.async_block_till_done()

    assert result["created"] is False
    body = only_notification(hass)
    assert "person.someone" in body
