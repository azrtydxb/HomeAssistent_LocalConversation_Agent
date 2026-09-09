"""Durable memory the agent keeps between conversations.

Home Assistant's chat log is a transcript: it belongs to one conversation and
expires with it, which is right. This is the other thing - what the household has
told the agent and it has chosen to keep.

Memory is shared by every agent on every provider, because a household has one
butler however many models are behind it. It lives in .storage, is capped, and is
injected into the prompt rather than fetched: a model does not know to ask for
something it does not know exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import llm
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util, ulid as ulid_util

from .const import DOMAIN, LOGGER

STORAGE_KEY = f"{DOMAIN}.memory"
STORAGE_VERSION = 1

# Every memory is paid for in prompt tokens on every single turn, and prompt size
# is what drives time to first token. A household that has told its assistant
# fifty things has told it enough.
MAX_MEMORIES = 50
MAX_MEMORY_LENGTH = 500

API_ID = f"{DOMAIN}.memory"
API_PROMPT = """
You can keep things between conversations.

Remember what will still be true tomorrow and matters later: who lives here, what
someone prefers, what has been decided. Do not remember the weather, the state of
a light, or anything you can look up.

Remember something when you are asked to, and when someone tells you a lasting
preference. Forget something when you are asked to, or when it is superseded.
"""


@dataclass
class MemoryStore:
    """The household's memories."""

    hass: HomeAssistant
    _store: Store = field(init=False)
    _memories: list[dict[str, Any]] = field(default_factory=list)
    _loaded: bool = False

    def __post_init__(self) -> None:
        """Create the backing store."""
        self._store = Store(self.hass, STORAGE_VERSION, STORAGE_KEY)

    async def async_load(self) -> None:
        """Read what was kept, once."""
        if self._loaded:
            return
        data = await self._store.async_load()
        self._memories = list(data.get("memories", [])) if data else []
        self._loaded = True

    async def async_add(self, text: str) -> dict[str, Any]:
        """Keep something, dropping the oldest if that is now too many."""
        await self.async_load()
        memory = {
            "id": ulid_util.ulid_now(),
            "text": text[:MAX_MEMORY_LENGTH],
            "created": dt_util.utcnow().isoformat(timespec="seconds"),
        }
        self._memories.append(memory)
        if len(self._memories) > MAX_MEMORIES:
            dropped = self._memories[: len(self._memories) - MAX_MEMORIES]
            self._memories = self._memories[-MAX_MEMORIES:]
            LOGGER.debug("Memory full, dropped %s oldest", len(dropped))
        await self._async_save()
        return memory

    async def async_remove(self, memory_id: str) -> bool:
        """Forget one thing. Returns whether there was anything to forget."""
        await self.async_load()
        remaining = [m for m in self._memories if m["id"] != memory_id]
        if len(remaining) == len(self._memories):
            return False
        self._memories = remaining
        await self._async_save()
        return True

    async def async_all(self) -> list[dict[str, Any]]:
        """Return everything kept, oldest first."""
        await self.async_load()
        return list(self._memories)

    async def _async_save(self) -> None:
        await self._store.async_save({"memories": self._memories})


@callback
def async_get_store(hass: HomeAssistant) -> MemoryStore:
    """Return the one store, creating it on first use."""
    if (store := hass.data.get(STORAGE_KEY)) is None:
        store = hass.data[STORAGE_KEY] = MemoryStore(hass)
    return store


def format_memories(memories: list[dict[str, Any]]) -> str:
    """Render memories for the prompt, or nothing at all when there are none."""
    if not memories:
        return ""
    lines = "\n".join(f"- {m['text']}" for m in memories)
    return (
        "\n\nWhat you have been told and kept. Treat it as current, act on it "
        f"without asking to be reminded, and do not recite it unasked:\n{lines}"
    )


class RememberTool(llm.Tool):
    """Keep something worth keeping."""

    name = "Remember"
    description = (
        "Keep one fact between conversations. Use for lasting things: who lives "
        "here, a preference, a decision. Not for anything you can look up."
    )
    parameters = vol.Schema({vol.Required("fact"): str})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        """Store one memory."""
        fact = str(tool_input.tool_args["fact"]).strip()
        if not fact:
            return {"error": "Nothing to remember."}
        memory = await async_get_store(hass).async_add(fact)
        return {"remembered": memory["text"], "id": memory["id"]}


class ForgetTool(llm.Tool):
    """Drop something that is no longer true."""

    name = "Forget"
    description = (
        "Forget one thing previously remembered, by its id. Use when asked to "
        "forget something, or when it has been superseded."
    )
    parameters = vol.Schema({vol.Required("id"): str})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        """Remove one memory."""
        memory_id = str(tool_input.tool_args["id"])
        if await async_get_store(hass).async_remove(memory_id):
            return {"forgotten": memory_id}
        return {"error": f"There is nothing remembered with id {memory_id}."}


class MemoryAPI(llm.API):
    """The tools that write memory. Reading happens through the prompt."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the API."""
        super().__init__(hass=hass, id=API_ID, name="Memory (remembers between chats)")

    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        """Return the tools."""
        return llm.APIInstance(
            api=self,
            api_prompt=API_PROMPT,
            llm_context=llm_context,
            tools=[RememberTool(), ForgetTool()],
        )
