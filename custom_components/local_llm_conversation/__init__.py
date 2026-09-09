"""The Local LLM Conversation integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import ChatCompletionsClient
from .const import CONF_BASE_URL, CONF_TIMEOUT, DEFAULT_TIMEOUT

PLATFORMS = [Platform.CONVERSATION]

type LocalLLMConfigEntry = ConfigEntry[ChatCompletionsClient]


async def async_setup_entry(hass: HomeAssistant, entry: LocalLLMConfigEntry) -> bool:
    """Set up Local LLM Conversation from a config entry."""
    entry.runtime_data = ChatCompletionsClient(
        async_get_clientsession(hass),
        entry.data[CONF_BASE_URL],
        entry.data.get(CONF_API_KEY),
        entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
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
