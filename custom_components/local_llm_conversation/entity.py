"""Shared machinery for the entities that talk to an endpoint.

Both the conversation agent and the AI Task entity drive the same loop; only what
they do with the finished chat log differs.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_LLM_HASS_API
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, llm
from homeassistant.helpers.entity import Entity

try:  # Home Assistant 2026.1 and later
    from probatio import to_openapi
except ImportError:  # Home Assistant 2025.9 to 2025.12
    from voluptuous_openapi import convert as to_openapi

from .client import CannotConnect, ChatCompletionsClient, InvalidAuth
from .const import (
    CONF_ADVANCED,
    CONF_ASSISTANT_NAME,
    CONF_LANGUAGE,
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_PROMPT,
    CONF_SUPPORTS_TOOLS,
    CONF_TOOL_MODE,
    CONF_TEMPERATURE,
    CONF_THINKING,
    CONF_TIMEOUT,
    CONF_TOP_P,
    CONF_VISION,
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_MAX_TOKENS,
    DEFAULT_SOUL,
    DEFAULT_TEMPERATURE,
    DEFAULT_THINKING,
    DEFAULT_TOP_P,
    DOMAIN,
    LOGGER,
    DEFAULT_TOOL_MODE,
    MAX_TOOL_ITERATIONS,
    REASONING_KEYS,
    TOOL_MODE_NONE,
    TOOL_MODE_PROMPTED,
)
from .streaming import (
    ThinkSplitter,
    ToolCallAccumulator,
    parse_prompted_tool_call,
    prompted_tool_prompt,
)


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


def _convert_content(
    content: conversation.Content, images: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Render one chat log entry as OpenAI messages.

    Assistant turns that called tools become a single assistant message; the
    results are separate ``tool`` messages linked by ``tool_call_id``.
    """
    if content.role == "system":
        return [{"role": "system", "content": content.content}]

    if content.role == "user":
        if attached := _attached_images(content, images or {}):
            # Multi-part form: text first, then each image the model can see.
            return [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": content.content},
                        *attached,
                    ],
                }
            ]
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


def _attached_images(
    content: conversation.UserContent, images: dict[str, str]
) -> list[dict[str, Any]]:
    """Return the user's attachments as OpenAI image parts."""
    return [
        {"type": "image_url", "image_url": {"url": url}}
        for attachment in content.attachments or []
        if (url := images.get(str(attachment.path)))
    ]


def _has_attachments(chat_log: conversation.ChatLog) -> bool:
    """Return whether this conversation carries anything to look at."""
    return any(
        content.role == "user" and bool(content.attachments)
        for content in chat_log.content
    )


async def _async_load_images(
    hass: HomeAssistant, chat_log: conversation.ChatLog
) -> dict[str, str]:
    """Read the conversation's image attachments into data URIs.

    Reading happens in the executor: these are camera snapshots on disk, and the
    event loop must not block on them. Anything that is not an image, or has
    since been deleted, is skipped rather than failing the turn.
    """
    wanted: dict[str, str] = {}
    for content in chat_log.content:
        if content.role != "user" or not content.attachments:
            continue
        for attachment in content.attachments:
            if not attachment.mime_type.startswith("image/"):
                LOGGER.debug("Ignoring non-image attachment %s", attachment.mime_type)
                continue
            wanted[str(attachment.path)] = attachment.mime_type

    if not wanted:
        return {}

    def read() -> dict[str, str]:
        loaded: dict[str, str] = {}
        for path, mime_type in wanted.items():
            try:
                raw = Path(path).read_bytes()
            except OSError as err:
                LOGGER.warning("Could not read attachment %s: %s", path, err)
                continue
            encoded = base64.b64encode(raw).decode("ascii")
            loaded[path] = f"data:{mime_type};base64,{encoded}"
        return loaded

    return await hass.async_add_executor_job(read)


async def _transform_stream(
    stream: AsyncGenerator[dict[str, Any]],
    prompted: bool = False,
) -> AsyncGenerator[conversation.AssistantContentDeltaDict]:
    """Translate endpoint SSE choices into chat log deltas.

    Tool calls are withheld until the stream ends because their arguments are
    only valid JSON once every fragment has arrived. In prompted mode the reply
    text is withheld too: whether it is an answer or a tool call cannot be known
    until it is complete, and half a tool call must never be spoken.
    """
    splitter = ThinkSplitter()
    tool_calls = ToolCallAccumulator()
    finish_reason: str | None = None
    produced_content = False
    prompted_text = ""

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
                elif prompted:
                    prompted_text += text
                elif spoken_text := spoken(text):
                    yield {"content": spoken_text}

        if raw_tool_calls := delta.get("tool_calls"):
            tool_calls.feed(raw_tool_calls)

    for kind, text in splitter.flush():
        if kind == "thinking":
            yield {"thinking_content": text}
        elif prompted:
            prompted_text += text
        elif spoken_text := spoken(text):
            yield {"content": spoken_text}

    completed = tool_calls.finish()

    if prompted and prompted_text:
        if call := parse_prompted_tool_call(prompted_text):
            name, arguments, _ = call
            completed = [*completed, ("", name, arguments)]
        elif spoken_text := spoken(prompted_text):
            yield {"content": spoken_text}

    # A reasoning model can spend its whole budget thinking and never answer,
    # which would otherwise surface as silence.
    if finish_reason == "length" and not produced_content and not completed:
        raise HomeAssistantError(
            "The model reached its token limit before answering. "
            "Increase 'Maximum tokens to return' in the integration options."
        )

    if completed:
        yield {"tool_calls": [_tool_input(*call) for call in completed]}


def _tool_input(call_id: str, name: str, arguments: str) -> llm.ToolInput:
    """Build a tool call, letting Home Assistant supply a missing id.

    A prompted call carries no id of its own, and an empty one would break the
    tool result's reference back to it.
    """
    args = _parse_arguments(name, arguments)
    if call_id:
        return llm.ToolInput(id=call_id, tool_name=name, tool_args=args)
    return llm.ToolInput(tool_name=name, tool_args=args)


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


class LocalLLMBaseEntity(Entity):
    """Everything the conversation agent and AI Task entity have in common."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: ConfigEntry, subentry: ConfigSubentry) -> None:
        """Initialize the entity."""
        self.entry = entry
        self.subentry = subentry
        self._attr_unique_id = subentry.subentry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Local LLM Conversation",
            model=subentry.data.get(CONF_MODEL),
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @callback
    def _async_set_available(self, available: bool) -> None:
        """Reflect the endpoint's reachability on the entity.

        Nothing polls these entities, so their state would otherwise claim they
        work right up until someone uses them.
        """
        if self._attr_available != available:
            self._attr_available = available
            self.async_write_ha_state()

    @property
    def _settings(self) -> dict[str, Any]:
        """Return this model's settings, advanced ones flattened in."""
        data = dict(self.subentry.data)
        return {**data.pop(CONF_ADVANCED, {}), **data}

    @property
    def _soul(self) -> str:
        """Return the persona prompt, with the name and any language filled in."""
        settings = self._settings
        soul = settings.get(CONF_PROMPT) or DEFAULT_SOUL
        name = settings.get(CONF_ASSISTANT_NAME) or DEFAULT_ASSISTANT_NAME
        soul = soul.replace("{name}", name)
        if language := settings.get(CONF_LANGUAGE):
            # Appended to the soul rather than sent per request, so the prompt
            # prefix stays identical between turns and keeps its cache.
            soul += (
                "\n\nAlways respond in the language identified by the IETF "
                f"language tag '{language}', whatever language you are "
                "addressed in."
            )
        return soul

    async def _async_handle_chat_log(
        self,
        chat_log: conversation.ChatLog,
        structure: vol.Schema | None = None,
    ) -> None:
        """Drive the endpoint until it stops asking for tools."""
        options = self._settings
        client: ChatCompletionsClient = self.entry.runtime_data

        mode = _tool_mode(options)
        tools: list[dict[str, Any]] | None = None
        if chat_log.llm_api and mode != TOOL_MODE_NONE:
            tools = [
                _format_tool(tool, chat_log.llm_api.custom_serializer)
                for tool in chat_log.llm_api.tools
            ]
        prompted = bool(tools) and mode == TOOL_MODE_PROMPTED

        # Whether this model reads images was settled by probing it when it was
        # chosen; the setting can then be turned off by hand.
        images = (
            await _async_load_images(self.hass, chat_log)
            if options.get(CONF_VISION) and _has_attachments(chat_log)
            else {}
        )

        for _iteration in range(MAX_TOOL_ITERATIONS):
            payload: dict[str, Any] = {
                "model": options[CONF_MODEL],
                "messages": [
                    message
                    for content in chat_log.content
                    for message in _convert_content(content, images)
                ],
                # Home Assistant's number selector stores whole numbers as
                # floats, and an endpoint expecting an integer rejects 4096.0
                # outright, so every turn fails once the form has been saved.
                "max_tokens": int(options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)),
                **_sampling(options),
            }
            if tools and not prompted:
                payload["tools"] = tools
            elif prompted:
                # The stack offers no tool calling, so the tools are described in
                # the prompt and the reply is parsed for one instead.
                payload["messages"][0] = {
                    **payload["messages"][0],
                    "content": payload["messages"][0]["content"]
                    + prompted_tool_prompt(tools),
                }
            if not options.get(CONF_THINKING, DEFAULT_THINKING):
                # How vLLM and SGLang switch off a reasoning model's think block.
                # Endpoints that ignore the hint simply keep reasoning, which the
                # stream already routes away from the spoken reply.
                payload["chat_template_kwargs"] = {"enable_thinking": False}
            if structure is not None:
                payload["response_format"] = _response_format(structure)

            if LOGGER.isEnabledFor(logging.DEBUG):
                # Only on request: this carries the exposed entity list and
                # whatever the household wrote into the soul.
                LOGGER.debug(
                    "Request to %s:\n%s",
                    options[CONF_MODEL],
                    json.dumps(payload, indent=2, default=str),
                )

            try:
                async for _content in chat_log.async_add_delta_content_stream(
                    self.entity_id,
                    _transform_stream(client.async_stream_chat(payload), prompted),
                ):
                    pass
            except InvalidAuth:
                # The key was accepted at setup and is not any more; only a
                # person can fix that, so ask for one.
                self.entry.async_start_reauth(self.hass)
                self._async_set_available(False)
                raise
            except CannotConnect:
                self._async_set_available(False)
                raise
            self._async_set_available(True)

            if not chat_log.unresponded_tool_results:
                break


def _response_format(structure: vol.Schema) -> dict[str, Any]:
    """Constrain the reply to a schema.

    Uses response_format, which vLLM honours. Its native guided_json is not used:
    proxies in front of it pass the field through untouched and the model then
    answers in prose, which looks like the constraint simply not working.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "structured_output",
            "schema": to_openapi(structure, custom_serializer=llm.selector_serializer),
            "strict": False,
        },
    }


def _tool_mode(options: dict[str, Any]) -> str:
    """Return how tools should be offered, honouring the old boolean setting."""
    if (mode := options.get(CONF_TOOL_MODE)) is not None:
        return mode
    # Before this was a choice, off meant the agent could not act at all.
    if options.get(CONF_SUPPORTS_TOOLS, True) is False:
        return TOOL_MODE_NONE
    return DEFAULT_TOOL_MODE


def _sampling(options: dict[str, Any]) -> dict[str, Any]:
    """Return the sampling parameters that were actually changed.

    Sending every parameter regardless is what most integrations do, and it makes
    a provider that refuses a combination fail on every turn with nothing to
    point at: Anthropic rejects a request carrying both temperature and top_p.
    A setting left alone is therefore left out, and the server's own default
    applies.
    """
    sampling = {}
    for key, default in (
        (CONF_TEMPERATURE, DEFAULT_TEMPERATURE),
        (CONF_TOP_P, DEFAULT_TOP_P),
    ):
        if (value := options.get(key, default)) != default:
            sampling[key] = value
    return sampling
