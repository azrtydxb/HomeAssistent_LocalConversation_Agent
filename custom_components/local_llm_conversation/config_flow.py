"""Config flow for Local LLM Conversation.

Two tiers: the config entry is the provider (endpoint and key), and each model on
it is a subentry with its own conversation agent.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API, CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    LanguageSelector,
    LanguageSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import CannotConnect, ChatCompletionsClient, InvalidAuth
from .const import (
    CONF_ADVANCED,
    CONF_ASSISTANT_NAME,
    CONF_LANGUAGE,
    CONF_BASE_URL,
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_PROMPT,
    CONF_TOOL_MODE,
    CONF_TEMPERATURE,
    CONF_THINKING,
    CONF_TIMEOUT,
    CONF_VISION,
    CONF_TOP_P,
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_MAX_TOKENS,
    DEFAULT_SOUL,
    DEFAULT_TEMPERATURE,
    DEFAULT_THINKING,
    DEFAULT_TIMEOUT,
    DEFAULT_TOOL_MODE,
    DEFAULT_TOP_P,
    DOMAIN,
    SUBENTRY_TYPE_AI_TASK,
    SUBENTRY_TYPE_CONVERSATION,
    TOOL_MODE_NATIVE,
    TOOL_MODE_NONE,
    TOOL_MODE_PROMPTED,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL): str,
        vol.Optional(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class LocalLLMConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a provider."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the endpoint and confirm it answers."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = ChatCompletionsClient(
                async_get_clientsession(self.hass),
                user_input[CONF_BASE_URL],
                user_input.get(CONF_API_KEY),
                DEFAULT_TIMEOUT,
            )
            try:
                await client.async_list_models()
            except InvalidAuth:
                errors[CONF_API_KEY] = "invalid_auth"
            except CannotConnect:
                errors[CONF_BASE_URL] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input[CONF_BASE_URL])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_BASE_URL], data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The endpoint rejected the stored key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new key, keeping the endpoint and its models."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            client = ChatCompletionsClient(
                async_get_clientsession(self.hass),
                entry.data[CONF_BASE_URL],
                user_input.get(CONF_API_KEY),
                DEFAULT_TIMEOUT,
            )
            try:
                await client.async_list_models()
            except InvalidAuth:
                errors[CONF_API_KEY] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
            description_placeholders={"base_url": entry.data[CONF_BASE_URL]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the endpoint or the key of an existing provider."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            client = ChatCompletionsClient(
                async_get_clientsession(self.hass),
                user_input[CONF_BASE_URL],
                user_input.get(CONF_API_KEY),
                DEFAULT_TIMEOUT,
            )
            try:
                await client.async_list_models()
            except InvalidAuth:
                errors[CONF_API_KEY] = "invalid_auth"
            except CannotConnect:
                errors[CONF_BASE_URL] = "cannot_connect"
            else:
                # Moving a provider to a new URL legitimately changes its unique
                # id; only a collision with a different provider is a problem.
                if any(
                    other.entry_id != entry.entry_id
                    and other.data.get(CONF_BASE_URL) == user_input[CONF_BASE_URL]
                    for other in self._async_current_entries()
                ):
                    return self.async_abort(reason="already_configured")
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=user_input[CONF_BASE_URL],
                    data_updates=user_input,
                    title=user_input[CONF_BASE_URL],
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or entry.data
            ),
            errors=errors,
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """A provider carries chat agents and task workers, one per model."""
        return {
            SUBENTRY_TYPE_CONVERSATION: ModelSubentryFlow,
            SUBENTRY_TYPE_AI_TASK: ModelSubentryFlow,
        }


class ModelSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one model on a provider."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._chosen: dict[str, Any] = {}
        self._vision: bool | None = None

    @property
    def _is_new(self) -> bool:
        return self.source == "user"

    def _current(self) -> dict[str, Any]:
        return {} if self._is_new else dict(self._get_reconfigure_subentry().data)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick a model, then ask the endpoint what that model can do."""
        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        current = self._current()

        if user_input is not None:
            self._chosen = user_input
            # Probing here rather than at first use means the result is visible
            # while configuring, and the setting follows the model you picked.
            self._vision = await entry.runtime_data.async_probe_vision(
                user_input[CONF_MODEL]
            )
            return await self.async_step_settings()

        try:
            models = await entry.runtime_data.async_list_models()
        except (CannotConnect, InvalidAuth):
            models = []
        if (chosen := current.get(CONF_MODEL)) and chosen not in models:
            models = [*models, chosen]

        schema: dict[Any, Any] = {}
        if self._is_new:
            # pylint: disable-next=home-assistant-config-flow-name-field
            schema[vol.Required(CONF_NAME, default=DEFAULT_CONVERSATION_NAME)] = str
        schema[
            vol.Required(
                CONF_MODEL, description={"suggested_value": current.get(CONF_MODEL)}
            )
        ] = SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(label=model, value=model) for model in models
                ],
                mode=SelectSelectorMode.DROPDOWN,
                custom_value=True,
                sort=True,
            )
        )
        return self.async_show_form(step_id="user", data_schema=vol.Schema(schema))

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Configure how the model behaves."""
        if user_input is not None:
            data = {**self._chosen, **user_input}
            title = data.pop(CONF_NAME, None)
            if self._is_new:
                return self.async_create_entry(
                    title=title or data[CONF_MODEL], data=data
                )
            return self.async_update_and_abort(
                self._get_entry(), self._get_reconfigure_subentry(), data=data
            )

        return self.async_show_form(
            step_id="settings",
            data_schema=self._schema(self._current()),
            description_placeholders={
                "model": self._chosen[CONF_MODEL],
                "vision": _describe_vision(self._vision),
            },
        )

    async_step_reconfigure = async_step_user

    @property
    def _is_conversation(self) -> bool:
        """A task worker generates data and has no persona to speak with."""
        return self._subentry_type == SUBENTRY_TYPE_CONVERSATION

    def _schema(self, current: dict[str, Any]) -> vol.Schema:
        """Build the form: everyday settings first, the rest folded away."""
        advanced = dict(current.get(CONF_ADVANCED, {}))
        # Never offer images to a model that has just been shown to ignore them,
        # even if they were on for whatever model this replaces. Where the probe
        # found support, a previous decision to keep them off still stands.
        if self._vision:
            vision_default = advanced.get(CONF_VISION, True)
        else:
            vision_default = False

        schema: dict[Any, Any] = {}
        if self._is_conversation:
            schema.update(
                {
                    vol.Optional(
                        CONF_LLM_HASS_API,
                        description={
                            "suggested_value": current.get(
                                CONF_LLM_HASS_API, [llm.LLM_API_ASSIST]
                            )
                        },
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(label=api.name, value=api.id)
                                for api in llm.async_get_apis(self.hass)
                            ],
                            multiple=True,
                        )
                    ),
                    vol.Required(
                        CONF_ASSISTANT_NAME,
                        default=current.get(
                            CONF_ASSISTANT_NAME, DEFAULT_ASSISTANT_NAME
                        ),
                    ): str,
                }
            )

        inner: dict[Any, Any] = {}
        if self._is_conversation:
            inner[
                vol.Optional(
                    CONF_PROMPT,
                    description={
                        "suggested_value": advanced.get(CONF_PROMPT, DEFAULT_SOUL)
                    },
                )
            ] = TemplateSelector()
        inner.update(
            {
                vol.Optional(
                    CONF_LANGUAGE,
                    description={"suggested_value": advanced.get(CONF_LANGUAGE)},
                ): LanguageSelector(LanguageSelectorConfig(native_name=True)),
                vol.Optional(CONF_VISION, default=vision_default): bool,
                vol.Optional(
                    CONF_THINKING,
                    default=advanced.get(CONF_THINKING, DEFAULT_THINKING),
                ): bool,
                vol.Optional(
                    CONF_MAX_TOKENS,
                    default=advanced.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS),
                ): NumberSelector(
                    NumberSelectorConfig(min=1, max=65536, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_TEMPERATURE,
                    default=advanced.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE),
                ): NumberSelector(NumberSelectorConfig(min=0, max=2, step=0.05)),
                vol.Optional(
                    CONF_TOP_P,
                    default=advanced.get(CONF_TOP_P, DEFAULT_TOP_P),
                ): NumberSelector(NumberSelectorConfig(min=0, max=1, step=0.05)),
                vol.Optional(
                    CONF_TIMEOUT,
                    default=advanced.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                ): NumberSelector(
                    NumberSelectorConfig(min=5, max=900, mode=NumberSelectorMode.BOX)
                ),
            }
        )
        if self._is_conversation:
            # A task worker is given no tool API, so the choice would do nothing.
            inner[
                vol.Optional(
                    CONF_TOOL_MODE,
                    default=advanced.get(CONF_TOOL_MODE, DEFAULT_TOOL_MODE),
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[TOOL_MODE_NATIVE, TOOL_MODE_PROMPTED, TOOL_MODE_NONE],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_TOOL_MODE,
                )
            )
        schema[vol.Required(CONF_ADVANCED)] = section(
            vol.Schema(inner), {"collapsed": True}
        )
        return vol.Schema(schema)


def _describe_vision(detected: bool | None) -> str:
    """Say what the probe found, in words a person can act on."""
    if detected is None:
        return "could not be determined - the endpoint reported no token usage"
    if detected:
        return "yes, this model reads images"
    return "no, this model ignores images"
