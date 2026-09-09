"""Conversation platform for Local LLM Conversation."""

from __future__ import annotations

from typing import Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LLM_HASS_API, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, SUBENTRY_TYPE_CONVERSATION
from .entity import LocalLLMBaseEntity
from .memory import API_ID as MEMORY_API_ID, async_get_store, format_memories


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one conversation entity per model configured on this provider."""
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_CONVERSATION:
            continue
        async_add_entities(
            [LocalLLMConversationEntity(entry, subentry)],
            config_subentry_id=subentry_id,
        )


class LocalLLMConversationEntity(conversation.ConversationEntity, LocalLLMBaseEntity):
    """Conversation agent backed by an OpenAI-compatible endpoint."""

    _attr_supports_streaming = True

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return the supported languages."""
        return MATCH_ALL

    async def _async_prompt(self) -> str:
        """Return the soul, with anything remembered added to it.

        Memories are put in the prompt rather than fetched with a tool: a model
        does not know to ask for something it does not know exists. They are
        added only when memory is switched on, so nothing appears from a setting
        someone did not choose.
        """
        prompt = self._soul
        if MEMORY_API_ID in (self._settings.get(CONF_LLM_HASS_API) or []):
            prompt += format_memories(await async_get_store(self.hass).async_all())
        return prompt

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Handle a turn of conversation."""
        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                self._settings.get(CONF_LLM_HASS_API),
                await self._async_prompt(),
                user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        await self._async_handle_chat_log(chat_log)

        return conversation.async_get_result_from_chat_log(user_input, chat_log)
