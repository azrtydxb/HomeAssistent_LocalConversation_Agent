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

from .client import ChatCompletionsClient
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
        vol.Optional(CONF_MODEL): str,
    }
)


class LocalLLMConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect connection details and verify the endpoint answers."""
        errors: dict[str, str] = {}
        models: list[str] = []

        if user_input is not None:
            client = ChatCompletionsClient(
                async_get_clientsession(self.hass),
                user_input[CONF_BASE_URL],
                user_input.get(CONF_API_KEY),
                DEFAULT_TIMEOUT,
            )
            models = await client.async_list_models()

            if not user_input.get(CONF_MODEL):
                # Endpoints serving exactly one model need no choice made.
                if len(models) == 1:
                    user_input[CONF_MODEL] = models[0]
                else:
                    errors[CONF_MODEL] = "model_required"

            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_MODEL],
                    data=user_input,
                    options={CONF_LLM_HASS_API: [llm.LLM_API_ASSIST]},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
            description_placeholders={"models": ", ".join(models) or "none"},
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
        apis = [
            SelectOptionDict(label=api.name, value=api.id)
            for api in llm.async_get_apis(self.hass)
        ]

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_MODEL,
                    default=options.get(
                        CONF_MODEL, self.config_entry.data.get(CONF_MODEL, "")
                    ),
                ): str,
                vol.Optional(
                    CONF_PROMPT, default=options.get(CONF_PROMPT, "")
                ): TemplateSelector(),
                vol.Optional(
                    CONF_LLM_HASS_API,
                    default=options.get(CONF_LLM_HASS_API, [llm.LLM_API_ASSIST]),
                ): SelectSelector(
                    SelectSelectorConfig(options=apis, multiple=True)
                ),
                vol.Optional(
                    CONF_MAX_TOKENS,
                    default=options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS),
                ): NumberSelector(
                    NumberSelectorConfig(min=1, max=65536, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_TEMPERATURE,
                    default=options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE),
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=2, step=0.05)
                ),
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
