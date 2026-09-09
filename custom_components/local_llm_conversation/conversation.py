"""Conversation platform for Local LLM Conversation."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Callable
from typing import Any, Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LLM_HASS_API, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, llm
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

try:  # Home Assistant 2026.1 and later
    from probatio import to_openapi
except ImportError:  # Home Assistant 2025.9 to 2025.12
    from voluptuous_openapi import convert as to_openapi

from .client import ChatCompletionsClient
from .const import (
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_PROMPT,
    CONF_SUPPORTS_TOOLS,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    DOMAIN,
    LOGGER,
    MAX_TOOL_ITERATIONS,
    REASONING_KEYS,
)
from .streaming import ThinkSplitter, ToolCallAccumulator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the conversation entity for a config entry."""
    async_add_entities([LocalLLMConversationEntity(entry)])


def _format_tool(
    tool: llm.Tool, custom_serializer: Callable[[Any], Any] | None
) -> dict[str, Any]:
    """Render a Home Assistant tool as an OpenAI function definition."""
    function: dict[str, Any] = {
        "name": tool.name,
        "parameters": to_openapi(tool.parameters, custom_serializer=custom_serializer),
    }
    if tool.description:
        function["description"] = tool.description
    return {"type": "function", "function": function}


def _convert_content(content: conversation.Content) -> list[dict[str, Any]]:
    """Render one chat log entry as OpenAI messages.

    Assistant turns that called tools become a single assistant message; the
    results are separate ``tool`` messages linked by ``tool_call_id``.
    """
    if content.role == "system":
        return [{"role": "system", "content": content.content}]

    if content.role == "user":
        return [{"role": "user", "content": content.content}]

    if content.role == "tool_result":
        return [
            {
                "role": "tool",
                "tool_call_id": content.tool_call_id,
                "content": json.dumps(content.tool_result),
            }
        ]

    # Assistant. thinking_content is deliberately dropped: replaying a model's
    # own reasoning back to it wastes context and degrades most local models.
    message: dict[str, Any] = {"role": "assistant", "content": content.content or ""}
    if content.tool_calls:
        message["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.tool_name,
                    "arguments": json.dumps(tool_call.tool_args),
                },
            }
            for tool_call in content.tool_calls
        ]
    return [message]


async def _transform_stream(
    stream: AsyncGenerator[dict[str, Any]],
) -> AsyncGenerator[conversation.AssistantContentDeltaDict]:
    """Translate endpoint SSE choices into chat log deltas.

    Tool calls are withheld until the stream ends because their arguments are
    only valid JSON once every fragment has arrived.
    """
    splitter = ThinkSplitter()
    tool_calls = ToolCallAccumulator()
    finish_reason: str | None = None
    produced_content = False

    def spoken(text: str) -> str:
        """Drop the blank lines models leave between reasoning and the answer."""
        nonlocal produced_content
        if not produced_content:
            text = text.lstrip()
        produced_content = produced_content or bool(text)
        return text

    yield {"role": "assistant"}

    async for choice in stream:
        delta = choice.get("delta") or {}
        finish_reason = choice.get("finish_reason") or finish_reason

        # Endpoints run with a reasoning parser expose thinking separately,
        # so it needs no splitting.
        for key in REASONING_KEYS:
            if reasoning := delta.get(key):
                yield {"thinking_content": reasoning}

        if content := delta.get("content"):
            for kind, text in splitter.feed(content):
                if kind == "thinking":
                    yield {"thinking_content": text}
                elif spoken_text := spoken(text):
                    yield {"content": spoken_text}

        if raw_tool_calls := delta.get("tool_calls"):
            tool_calls.feed(raw_tool_calls)

    for kind, text in splitter.flush():
        if kind == "thinking":
            yield {"thinking_content": text}
        elif spoken_text := spoken(text):
            yield {"content": spoken_text}

    completed = tool_calls.finish()

    # A reasoning model can spend its whole budget thinking and never answer,
    # which would otherwise surface as silence.
    if finish_reason == "length" and not produced_content and not completed:
        raise HomeAssistantError(
            "The model reached its token limit before answering. "
            "Increase 'Maximum tokens to return' in the integration options."
        )

    if completed:
        yield {
            "tool_calls": [
                llm.ToolInput(
                    id=call_id,
                    tool_name=name,
                    tool_args=_parse_arguments(name, arguments),
                )
                for call_id, name, arguments in completed
            ]
        }


def _parse_arguments(name: str, arguments: str) -> dict[str, Any]:
    """Parse a tool call's argument JSON, failing loudly rather than silently."""
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as err:
        LOGGER.error("Model produced invalid arguments for %s: %s", name, arguments)
        raise HomeAssistantError(
            f"The model produced invalid arguments for tool {name}"
        ) from err
    if not isinstance(parsed, dict):
        raise HomeAssistantError(
            f"The model produced non-object arguments for tool {name}"
        )
    return parsed


class LocalLLMConversationEntity(conversation.ConversationEntity):
    """Conversation agent backed by an OpenAI-compatible endpoint."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = True

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the agent."""
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Local LLM Conversation",
            model=entry.options.get(CONF_MODEL) or entry.data.get(CONF_MODEL),
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return the supported languages."""
        return MATCH_ALL

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Handle a turn of conversation."""
        options = self.entry.options

        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                options.get(CONF_LLM_HASS_API),
                options.get(CONF_PROMPT),
                user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        client: ChatCompletionsClient = self.entry.runtime_data
        tools: list[dict[str, Any]] | None = None
        if chat_log.llm_api and options.get(CONF_SUPPORTS_TOOLS, True):
            tools = [
                _format_tool(tool, chat_log.llm_api.custom_serializer)
                for tool in chat_log.llm_api.tools
            ]

        for _iteration in range(MAX_TOOL_ITERATIONS):
            payload: dict[str, Any] = {
                "model": options.get(CONF_MODEL) or self.entry.data[CONF_MODEL],
                "messages": [
                    message
                    for content in chat_log.content
                    for message in _convert_content(content)
                ],
                "max_tokens": options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS),
                "temperature": options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE),
                "top_p": options.get(CONF_TOP_P, DEFAULT_TOP_P),
            }
            if tools:
                payload["tools"] = tools

            async for _content in chat_log.async_add_delta_content_stream(
                self.entity_id, _transform_stream(client.async_stream_chat(payload))
            ):
                pass

            if not chat_log.unresponded_tool_results:
                break

        return conversation.async_get_result_from_chat_log(user_input, chat_log)
