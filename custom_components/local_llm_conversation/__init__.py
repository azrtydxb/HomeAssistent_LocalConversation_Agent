"""The Local LLM Conversation integration.

A config entry is a provider - one OpenAI-compatible endpoint. Each model on that
provider is a subentry with its own conversation agent, so a household can run a
fast model for voice and a larger one for harder questions side by side.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import ChatCompletionsClient
from .const import (
    CONF_ADVANCED,
    CONF_BASE_URL,
    CONF_MODEL,
    CONF_TIMEOUT,
    DEFAULT_TIMEOUT,
    LOGGER,
    SUBENTRY_TYPE_CONVERSATION,
)

PLATFORMS = [Platform.CONVERSATION]

type LocalLLMConfigEntry = ConfigEntry[ChatCompletionsClient]

# Options that live inside the collapsed Advanced section of the model form.
ADVANCED_KEYS = (
    "prompt",
    "max_tokens",
    "temperature",
    "top_p",
    CONF_TIMEOUT,
    "supports_tools",
)


async def async_setup_entry(hass: HomeAssistant, entry: LocalLLMConfigEntry) -> bool:
    """Set up a provider from a config entry."""
    entry.runtime_data = ChatCompletionsClient(
        async_get_clientsession(hass),
        entry.data[CONF_BASE_URL],
        entry.data.get(CONF_API_KEY),
        DEFAULT_TIMEOUT,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LocalLLMConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: LocalLLMConfigEntry) -> None:
    """Reload the entry so option changes take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate a flat entry to the provider/model layout.

    Version 1 held the endpoint and one model in a single entry. Version 2 keeps
    only the endpoint, and moves the model and its settings into a subentry, so
    further models can be added to the same provider.
    """
    # A higher version is refused by Home Assistant before this is reached.
    if entry.version == 1:
        LOGGER.debug("Migrating %s to the provider/model layout", entry.title)
        data = dict(entry.data)
        options = dict(entry.options)
        model = data.pop(CONF_MODEL, "")

        advanced = {key: options[key] for key in ADVANCED_KEYS if key in options}
        subentry_data = {
            CONF_MODEL: options.get(CONF_MODEL) or model,
            CONF_LLM_HASS_API: options.get(CONF_LLM_HASS_API, []),
            CONF_ADVANCED: advanced,
        }

        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options={},
            title=entry.data.get(CONF_BASE_URL, entry.title),
            version=2,
        )
        hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                data=subentry_data,
                subentry_type=SUBENTRY_TYPE_CONVERSATION,
                title=model or entry.title,
                unique_id=None,
            ),
        )

    return True
