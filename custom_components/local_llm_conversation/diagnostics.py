"""Diagnostics for Local LLM Conversation."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from . import LocalLLMConfigEntry
from .client import CannotConnect, InvalidAuth
from .const import CONF_ADVANCED, CONF_PROMPT

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LocalLLMConfigEntry
) -> dict[str, Any]:
    """Return what a bug report needs, without the key.

    The soul is a free-text field a household may have made personal, so it is
    reported by length rather than content.
    """
    try:
        models: list[str] | str = await entry.runtime_data.async_list_models()
    except InvalidAuth:
        models = "endpoint rejected the API key"
    except CannotConnect as err:
        models = f"endpoint unreachable: {err}"

    return {
        "entry": {
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "has_api_key": bool(entry.data.get(CONF_API_KEY)),
        },
        "models_offered": models,
        "subentries": [
            {
                "type": subentry.subentry_type,
                "data": _redact_subentry(dict(subentry.data)),
            }
            for subentry in entry.subentries.values()
        ],
    }


def _redact_subentry(data: dict[str, Any]) -> dict[str, Any]:
    """Replace the persona with its size; keep every setting that matters."""
    advanced = dict(data.get(CONF_ADVANCED, {}))
    if prompt := advanced.pop(CONF_PROMPT, None):
        advanced["prompt_length"] = len(prompt)
    return {**data, CONF_ADVANCED: advanced}
