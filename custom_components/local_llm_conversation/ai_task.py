"""AI Task platform for Local LLM Conversation.

Lets automations ask the model for data - a shopping list from a photo of the
fridge, a summary of the day - rather than only holding a conversation.
"""

from __future__ import annotations

from json import JSONDecodeError

from homeassistant.components import ai_task, conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.json import json_loads

from .const import LOGGER, SUBENTRY_TYPE_AI_TASK
from .entity import LocalLLMBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one task entity per AI Task model on this provider."""
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_AI_TASK:
            continue
        async_add_entities(
            [LocalLLMTaskEntity(entry, subentry)], config_subentry_id=subentry_id
        )


class LocalLLMTaskEntity(ai_task.AITaskEntity, LocalLLMBaseEntity):
    """Generates data with a local model."""

    _attr_supported_features = (
        ai_task.AITaskEntityFeature.GENERATE_DATA
        | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
    )

    async def _async_generate_data(
        self,
        task: ai_task.GenDataTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenDataTaskResult:
        """Run the task and return what the model produced."""
        await self._async_handle_chat_log(chat_log, task.structure)

        last = chat_log.content[-1]
        if not isinstance(last, conversation.AssistantContent):
            raise HomeAssistantError("The model produced no answer")
        text = last.content or ""

        if not task.structure:
            return ai_task.GenDataTaskResult(
                conversation_id=chat_log.conversation_id, data=text
            )

        try:
            data = json_loads(text)
        except JSONDecodeError as err:
            # The schema was sent as response_format; an endpoint that ignores
            # it answers in prose, which is worth saying plainly.
            LOGGER.error("Expected JSON matching the task structure, got: %s", text)
            raise HomeAssistantError(
                "The model did not return data matching the requested structure"
            ) from err

        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id, data=data
        )
