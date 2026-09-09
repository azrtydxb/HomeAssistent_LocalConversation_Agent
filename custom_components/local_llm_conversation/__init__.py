"""The Local LLM Conversation integration.

A config entry is a provider - one OpenAI-compatible endpoint. Each model on that
provider is a subentry with its own conversation agent, so a household can run a
fast model for voice and a larger one for harder questions side by side.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    llm,
)
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import CannotConnect, ChatCompletionsClient, InvalidAuth
from .history_api import HistoryAPI
from .const import (
    CONF_ADVANCED,
    CONF_BASE_URL,
    CONF_MODEL,
    CONF_TIMEOUT,
    DEFAULT_TIMEOUT,
    DOMAIN,
    LOGGER,
    SUBENTRY_TYPE_CONVERSATION,
)

PLATFORMS = [Platform.AI_TASK, Platform.CONVERSATION]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

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


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the tools this integration adds to Home Assistant.

    Registered once for the whole integration rather than per provider: an API is
    a set of tools, not a connection, and registering it twice fails.
    """
    llm.async_register_api(hass, HistoryAPI(hass))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LocalLLMConfigEntry) -> bool:
    """Set up a provider from a config entry."""
    client = ChatCompletionsClient(
        async_get_clientsession(hass),
        entry.data[CONF_BASE_URL],
        entry.data.get(CONF_API_KEY),
        DEFAULT_TIMEOUT,
    )

    # Reach the endpoint once before claiming the agents work. A dead endpoint
    # is retried by Home Assistant with backoff; a rejected key needs a person.
    try:
        await client.async_list_models()
    except InvalidAuth as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except CannotConnect as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LocalLLMConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: LocalLLMConfigEntry) -> None:
    """Reload the entry so option changes take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting a device that no longer belongs to a model.

    Devices are keyed by subentry. One that matches no current subentry is left
    over - from an upgrade, or from a model removed while the entry was not
    loaded - and there is otherwise no way to clear it from the UI. A device that
    does belong to a model stays: remove the model instead.
    """
    live = {
        (DOMAIN, subentry_id)
        for subentry_id, subentry in entry.subentries.items()
        if subentry.subentry_type == SUBENTRY_TYPE_CONVERSATION
    }
    return not (device.identifiers & live)


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

        subentry = ConfigSubentry(
            data=subentry_data,
            subentry_type=SUBENTRY_TYPE_CONVERSATION,
            title=model or entry.title,
            unique_id=None,
        )
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options={},
            title=entry.data.get(CONF_BASE_URL, entry.title),
            version=2,
        )
        hass.config_entries.async_add_subentry(entry, subentry)
        _async_adopt_existing_entity(hass, entry, subentry)

    return True


@callback
def _async_adopt_existing_entity(
    hass: HomeAssistant, entry: ConfigEntry, subentry: ConfigSubentry
) -> None:
    """Move the agent that already exists onto the new subentry.

    The entity was keyed by the config entry and is now keyed by the subentry.
    Left alone, the old registration is orphaned and the new agent appears beside
    it under a suffixed entity id - so every automation and voice pipeline would
    still point at an agent that no longer answers.
    """
    entities = er.async_get(hass)
    for registration in er.async_entries_for_config_entry(entities, entry.entry_id):
        if registration.unique_id != entry.entry_id:
            continue
        entities.async_update_entity(
            registration.entity_id,
            new_unique_id=subentry.subentry_id,
            config_subentry_id=subentry.subentry_id,
        )

    devices = dr.async_get(hass)
    if device := devices.async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    ):
        devices.async_update_device(
            device.id,
            new_identifiers={(DOMAIN, subentry.subentry_id)},
            add_config_subentry_id=subentry.subentry_id,
        )
