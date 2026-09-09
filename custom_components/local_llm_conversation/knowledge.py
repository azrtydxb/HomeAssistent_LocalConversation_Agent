"""Reusable knowledge the household writes once and enables per agent.

The soul says who the agent is. This is what it knows about this particular
house: how the heating actually works, what an oddly named sensor means, the
rules for the holiday cottage.

Kept deliberately small. Files in a directory, enabled per model, appended to the
prompt. No download service and no registry: every enabled file is paid for in
prompt tokens on every single utterance, including "turn on the kitchen light",
and prompt size is what drives time to first token.
"""

from __future__ import annotations

from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import DOMAIN, LOGGER

KNOWLEDGE_DIR = f"{DOMAIN}/knowledge"
SUFFIX = ".md"
# A file this size already costs noticeable prefill on every turn.
MAX_FILE_BYTES = 32 * 1024


def knowledge_path(hass: HomeAssistant) -> Path:
    """Return the directory knowledge files are read from."""
    return Path(hass.config.path(KNOWLEDGE_DIR))


def _list_files(directory: Path) -> list[str]:
    """Return the available knowledge file names, without their suffix."""
    if not directory.is_dir():
        return []
    return sorted(
        path.stem
        for path in directory.glob(f"*{SUFFIX}")
        if path.is_file() and not path.name.startswith(".")
    )


async def async_available(hass: HomeAssistant) -> list[str]:
    """Return what the household has written, for the options form."""
    return await hass.async_add_executor_job(_list_files, knowledge_path(hass))


def _read(directory: Path, names: list[str]) -> str:
    """Read the named files, skipping anything missing or too large."""
    parts: list[str] = []
    for name in names:
        # Names come from a stored setting, so refuse anything that could climb
        # out of the directory rather than trusting it.
        if Path(name).name != name:
            LOGGER.warning("Ignoring knowledge entry with a path in it: %s", name)
            continue
        path = directory / f"{name}{SUFFIX}"
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                LOGGER.warning(
                    "Knowledge file %s is over %s bytes and was skipped",
                    path.name,
                    MAX_FILE_BYTES,
                )
                continue
            text = path.read_text(encoding="utf-8").strip()
        except OSError as err:
            LOGGER.warning("Could not read knowledge file %s: %s", path.name, err)
            continue
        if text:
            parts.append(f"## {name}\n{text}")
    return "\n\n".join(parts)


async def async_render(hass: HomeAssistant, names: list[str]) -> str:
    """Return the enabled knowledge, ready to append to the prompt."""
    if not names:
        # Not a behaviour, an optimisation: without this every turn of every
        # conversation pays for an executor hop to read nothing.
        return ""
    body = await hass.async_add_executor_job(_read, knowledge_path(hass), names)
    if not body:
        return ""
    return f"\n\nWhat you know about this house:\n\n{body}"
