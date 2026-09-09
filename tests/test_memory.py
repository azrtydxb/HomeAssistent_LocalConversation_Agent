"""Tests for memory that survives a conversation."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component

from custom_components.local_llm_conversation.memory import (
    MAX_MEMORIES,
    ForgetTool,
    RememberTool,
    async_get_store,
    format_memories,
)


@pytest.fixture(autouse=True)
async def setup_dependencies(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "homeassistant", {})


def call(tool, **args):
    return llm.ToolInput(tool_name=tool.name, tool_args=args)


def context() -> llm.LLMContext:
    return llm.LLMContext(
        platform="local_llm_conversation",
        context=None,
        language="en",
        assistant="conversation",
        device_id=None,
    )


async def test_something_remembered_is_still_there_later(hass: HomeAssistant) -> None:
    """The whole point: it outlives the conversation that created it."""
    result = await RememberTool().async_call(
        hass, call(RememberTool, fact="Pascal takes his tea black"), context()
    )
    assert result["remembered"] == "Pascal takes his tea black"

    kept = await async_get_store(hass).async_all()
    assert [m["text"] for m in kept] == ["Pascal takes his tea black"]


async def test_it_survives_a_reload(hass: HomeAssistant) -> None:
    """Memory held only in memory is not memory."""
    from homeassistant.helpers.storage import Store

    from custom_components.local_llm_conversation.memory import (
        STORAGE_KEY,
        STORAGE_VERSION,
    )

    await RememberTool().async_call(
        hass, call(RememberTool, fact="the cat is called Boots"), context()
    )
    await hass.async_block_till_done()

    # Read the store directly, as a fresh Home Assistant would.
    raw = await Store(hass, STORAGE_VERSION, STORAGE_KEY).async_load()
    assert [m["text"] for m in raw["memories"]] == ["the cat is called Boots"]


async def test_forgetting_removes_only_that_one(hass: HomeAssistant) -> None:
    first = await RememberTool().async_call(
        hass, call(RememberTool, fact="one"), context()
    )
    await RememberTool().async_call(hass, call(RememberTool, fact="two"), context())

    assert (
        await ForgetTool().async_call(hass, call(ForgetTool, id=first["id"]), context())
    )["forgotten"] == first["id"]
    assert [m["text"] for m in await async_get_store(hass).async_all()] == ["two"]


async def test_forgetting_something_unknown_says_so(hass: HomeAssistant) -> None:
    result = await ForgetTool().async_call(
        hass, call(ForgetTool, id="not-a-real-id"), context()
    )
    assert "nothing remembered" in result["error"]


async def test_an_empty_fact_is_refused(hass: HomeAssistant) -> None:
    result = await RememberTool().async_call(
        hass, call(RememberTool, fact="   "), context()
    )
    assert "error" in result
    assert await async_get_store(hass).async_all() == []


async def test_memory_is_capped_and_drops_the_oldest(hass: HomeAssistant) -> None:
    """Every memory is paid for in prompt tokens on every single turn.

    Unbounded memory becomes unbounded prompt, and prompt size is what drives
    time to first token.
    """
    store = async_get_store(hass)
    for i in range(MAX_MEMORIES + 5):
        await store.async_add(f"fact {i}")

    kept = [m["text"] for m in await store.async_all()]
    assert len(kept) == MAX_MEMORIES
    assert kept[0] == "fact 5"
    assert kept[-1] == f"fact {MAX_MEMORIES + 4}"


async def test_a_very_long_fact_is_trimmed(hass: HomeAssistant) -> None:
    from custom_components.local_llm_conversation.memory import MAX_MEMORY_LENGTH

    memory = await async_get_store(hass).async_add("x" * 5000)
    assert len(memory["text"]) == MAX_MEMORY_LENGTH


def test_no_memories_adds_nothing_to_the_prompt() -> None:
    """An empty section would cost tokens and invite the model to fill it."""
    assert format_memories([]) == ""


def test_memories_are_rendered_for_the_prompt() -> None:
    rendered = format_memories(
        [{"text": "the cat is called Boots"}, {"text": "bins go out Tuesday"}]
    )
    assert "- the cat is called Boots" in rendered
    assert "- bins go out Tuesday" in rendered
    assert "do not recite it unasked" in rendered


async def test_memories_reach_the_prompt_only_when_memory_is_switched_on(
    hass: HomeAssistant,
) -> None:
    """Nothing should appear in the prompt from a setting nobody chose."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.local_llm_conversation.const import (
        CONF_BASE_URL,
        CONF_MODEL,
        DOMAIN,
        SUBENTRY_TYPE_CONVERSATION,
    )
    from custom_components.local_llm_conversation.conversation import (
        LocalLLMConversationEntity,
    )
    from custom_components.local_llm_conversation.memory import API_ID

    await async_get_store(hass).async_add("the cat is called Boots")

    def agent_with(apis: list[str]) -> LocalLLMConversationEntity:
        entry = MockConfigEntry(
            domain=DOMAIN,
            version=2,
            data={CONF_BASE_URL: "http://localhost:8000"},
            subentries_data=[
                {
                    "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                    "title": "Voice",
                    "unique_id": None,
                    "data": {CONF_MODEL: "qwen3", "llm_hass_api": apis},
                }
            ],
        )
        entry.add_to_hass(hass)
        agent = LocalLLMConversationEntity(entry, next(iter(entry.subentries.values())))
        agent.hass = hass
        return agent

    without = await agent_with(["assist"])._async_prompt()
    assert "Boots" not in without

    with_memory = await agent_with(["assist", API_ID])._async_prompt()
    assert "the cat is called Boots" in with_memory
    # And the persona is still there, not replaced by it.
    assert "Jarvis" in with_memory
