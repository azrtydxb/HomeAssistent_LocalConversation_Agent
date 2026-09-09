"""Config flow for Local LLM Conversation."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API
from homeassistant.core import callback
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
    CONF_BASE_URL,
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_PROMPT,
    CONF_SUPPORTS_TOOLS,
    CONF_TEMPERATURE,
    CONF_TIMEOUT,
    CONF_TOP_P,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT,
    DEFAULT_TOP_P,
    DOMAIN,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL): str,
        vol.Optional(CONF_API_KEY): str,
    }
)


class LocalLLMConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._connection: dict[str, Any] = {}
        self._models: list[str] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the endpoint, then ask it which models it serves."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = ChatCompletionsClient(
                async_get_clientsession(self.hass),
                user_input[CONF_BASE_URL],
                user_input.get(CONF_API_KEY),
                DEFAULT_TIMEOUT,
            )
            try:
                self._models = await client.async_list_models()
            except InvalidAuth:
                errors[CONF_API_KEY] = "invalid_auth"
            except CannotConnect:
                errors[CONF_BASE_URL] = "cannot_connect"
            else:
                self._connection = user_input
                return await self.async_step_model()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which of the endpoint's models to talk to."""
        if user_input is not None:
            model = user_input[CONF_MODEL].strip()
            await self.async_set_unique_id(
                f"{self._connection[CONF_BASE_URL]}::{model}"
            )
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=model,
                data={**self._connection, CONF_MODEL: model},
                options={CONF_LLM_HASS_API: [llm.LLM_API_ASSIST]},
            )

        # custom_value keeps the field usable when the endpoint lists nothing, or
        # lists a name the proxy does not actually route.
        schema = vol.Schema(
            {
                vol.Required(CONF_MODEL): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(label=model, value=model)
                            for model in self._models
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                        sort=True,
                    )
                )
            }
        )
        if len(self._models) == 1:
            schema = self.add_suggested_values_to_schema(
                schema, {CONF_MODEL: self._models[0]}
            )

        return self.async_show_form(
            step_id="model",
            data_schema=schema,
            description_placeholders={"count": str(len(self._models))},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return LocalLLMOptionsFlow()


class LocalLLMOptionsFlow(OptionsFlow):
    """Handle options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the agent's behaviour."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        current_model = options.get(
            CONF_MODEL, self.config_entry.data.get(CONF_MODEL, "")
        )

        # Offer the endpoint's current catalogue, but never block on it: the
        # options form must open even when the endpoint is down.
        client = ChatCompletionsClient(
            async_get_clientsession(self.hass),
            self.config_entry.data[CONF_BASE_URL],
            self.config_entry.data.get(CONF_API_KEY),
            DEFAULT_TIMEOUT,
        )
        try:
            models = await client.async_list_models()
        except (CannotConnect, InvalidAuth):
            models = []
        if current_model and current_model not in models:
            models = [*models, current_model]

        apis = [
            SelectOptionDict(label=api.name, value=api.id)
            for api in llm.async_get_apis(self.hass)
        ]

        schema = vol.Schema(
            {
                vol.Required(CONF_MODEL, default=current_model): SelectSelector(
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
                    CONF_PROMPT, default=options.get(CONF_PROMPT, "")
                ): TemplateSelector(),
                vol.Optional(
                    CONF_LLM_HASS_API,
                    default=options.get(CONF_LLM_HASS_API, [llm.LLM_API_ASSIST]),
                ): SelectSelector(SelectSelectorConfig(options=apis, multiple=True)),
                vol.Optional(
                    CONF_MAX_TOKENS,
                    default=options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS),
                ): NumberSelector(
                    NumberSelectorConfig(min=1, max=65536, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_TEMPERATURE,
                    default=options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE),
                ): NumberSelector(NumberSelectorConfig(min=0, max=2, step=0.05)),
                vol.Optional(
                    CONF_TOP_P, default=options.get(CONF_TOP_P, DEFAULT_TOP_P)
                ): NumberSelector(NumberSelectorConfig(min=0, max=1, step=0.05)),
                vol.Optional(
                    CONF_TIMEOUT, default=options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
                ): NumberSelector(
                    NumberSelectorConfig(min=5, max=900, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_SUPPORTS_TOOLS,
                    default=options.get(CONF_SUPPORTS_TOOLS, True),
                ): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
