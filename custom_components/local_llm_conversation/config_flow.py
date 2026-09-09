"""Config flow for Local LLM Conversation.

Two tiers: the config entry is the provider (endpoint and key), and each model on
it is a subentry with its own conversation agent.
"""

from __future__ import annotations

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
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
)

from .client import CannotConnect, ChatCompletionsClient, InvalidAuth
from .const import (
    CONF_ADVANCED,
    CONF_ASSISTANT_NAME,
    CONF_BASE_URL,
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_PROMPT,
    CONF_SUPPORTS_TOOLS,
    CONF_TEMPERATURE,
    CONF_TIMEOUT,
    CONF_TOP_P,
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_MAX_TOKENS,
    DEFAULT_SOUL,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT,
    DEFAULT_TOP_P,
    DOMAIN,
    SUBENTRY_TYPE_CONVERSATION,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL): str,
        vol.Optional(CONF_API_KEY): str,
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
        """Each model on the provider is a conversation subentry."""
        return {SUBENTRY_TYPE_CONVERSATION: ConversationSubentryFlow}


class ConversationSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one model on a provider."""

    @property
    def _is_new(self) -> bool:
        return self.source == "user"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Configure a model."""
        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        if user_input is not None:
            title = user_input.pop(CONF_NAME, None)
            if self._is_new:
                return self.async_create_entry(
                    title=title or user_input[CONF_MODEL], data=user_input
                )
            return self.async_update_and_abort(
                entry, self._get_reconfigure_subentry(), data=user_input
            )

        current = {} if self._is_new else dict(self._get_reconfigure_subentry().data)

        try:
            models = await entry.runtime_data.async_list_models()
        except (CannotConnect, InvalidAuth):
            models = []
        if (chosen := current.get(CONF_MODEL)) and chosen not in models:
            models = [*models, chosen]

        return self.async_show_form(
            step_id="user",
            data_schema=self._schema(models, current),
        )

    async_step_reconfigure = async_step_user

    def _schema(self, models: list[str], current: dict[str, Any]) -> vol.Schema:
        """Build the form: everyday settings first, the rest folded away."""
        advanced = dict(current.get(CONF_ADVANCED, {}))
        schema: dict[Any, Any] = {}

        if self._is_new:
            # pylint: disable-next=home-assistant-config-flow-name-field
            schema[vol.Required(CONF_NAME, default=DEFAULT_CONVERSATION_NAME)] = str

        schema.update(
            {
                vol.Required(
                    CONF_MODEL,
                    description={"suggested_value": current.get(CONF_MODEL)},
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(label=model, value=model)
                            for model in models
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                        sort=True,
                    )
                ),
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
                    default=current.get(CONF_ASSISTANT_NAME, DEFAULT_ASSISTANT_NAME),
                ): str,
                vol.Required(CONF_ADVANCED): section(
                    vol.Schema(
                        {
                            vol.Optional(
                                CONF_PROMPT,
                                description={
                                    "suggested_value": advanced.get(
                                        CONF_PROMPT, DEFAULT_SOUL
                                    )
                                },
                            ): TemplateSelector(),
                            vol.Optional(
                                CONF_MAX_TOKENS,
                                default=advanced.get(
                                    CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS
                                ),
                            ): NumberSelector(
                                NumberSelectorConfig(
                                    min=1, max=65536, mode=NumberSelectorMode.BOX
                                )
                            ),
                            vol.Optional(
                                CONF_TEMPERATURE,
                                default=advanced.get(
                                    CONF_TEMPERATURE, DEFAULT_TEMPERATURE
                                ),
                            ): NumberSelector(
                                NumberSelectorConfig(min=0, max=2, step=0.05)
                            ),
                            vol.Optional(
                                CONF_TOP_P,
                                default=advanced.get(CONF_TOP_P, DEFAULT_TOP_P),
                            ): NumberSelector(
                                NumberSelectorConfig(min=0, max=1, step=0.05)
                            ),
                            vol.Optional(
                                CONF_TIMEOUT,
                                default=advanced.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                            ): NumberSelector(
                                NumberSelectorConfig(
                                    min=5, max=900, mode=NumberSelectorMode.BOX
                                )
                            ),
                            vol.Optional(
                                CONF_SUPPORTS_TOOLS,
                                default=advanced.get(CONF_SUPPORTS_TOOLS, True),
                            ): bool,
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )
        return vol.Schema(schema)
