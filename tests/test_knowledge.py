"""Tests for the knowledge files a household writes."""

from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.local_llm_conversation.knowledge import (
    MAX_FILE_BYTES,
    async_available,
    async_render,
    knowledge_path,
)


@pytest.fixture(autouse=True)
async def setup_dependencies(hass: HomeAssistant) -> None:
    """Set up. The knowledge directory is emptied by an autouse fixture."""
    assert await async_setup_component(hass, "homeassistant", {})


def write(hass: HomeAssistant, name: str, text: str) -> Path:
    directory = knowledge_path(hass)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


async def test_nothing_written_offers_nothing(hass: HomeAssistant) -> None:
    """An empty picker is a question with no answers."""
    assert await async_available(hass) == []
    assert await async_render(hass, []) == ""


async def test_files_are_offered_by_name(hass: HomeAssistant) -> None:
    write(hass, "heating.md", "The heating is zoned.")
    write(hass, "cottage.md", "The cottage is unheated in winter.")
    write(hass, "notes.txt", "not markdown")
    write(hass, ".hidden.md", "hidden")

    assert await async_available(hass) == ["cottage", "heating"]


async def test_enabled_files_reach_the_prompt(hass: HomeAssistant) -> None:
    write(hass, "heating.md", "The heating is zoned per floor.")
    write(hass, "cottage.md", "The cottage is unheated in winter.")

    rendered = await async_render(hass, ["heating"])

    assert "The heating is zoned per floor." in rendered
    assert "## heating" in rendered
    # Only what was enabled.
    assert "cottage" not in rendered


async def test_nothing_enabled_costs_nothing(hass: HomeAssistant) -> None:
    """Prompt size drives time to first token; an empty section is not free."""
    write(hass, "heating.md", "The heating is zoned.")
    assert await async_render(hass, []) == ""


async def test_a_missing_file_does_not_break_the_turn(hass: HomeAssistant) -> None:
    """A file can be deleted while still enabled in a saved setting."""
    write(hass, "heating.md", "The heating is zoned.")
    rendered = await async_render(hass, ["heating", "deleted"])
    assert "The heating is zoned." in rendered
    assert "deleted" not in rendered


async def test_an_oversized_file_is_skipped(hass: HomeAssistant) -> None:
    """It would be paid for on every single utterance."""
    write(hass, "huge.md", "x" * (MAX_FILE_BYTES + 1))
    write(hass, "small.md", "fine")

    rendered = await async_render(hass, ["huge", "small"])
    assert "fine" in rendered
    assert "x" * 100 not in rendered


async def test_a_name_that_climbs_out_of_the_directory_is_refused(
    hass: HomeAssistant,
) -> None:
    """The names come from a stored setting, not from the picker, at read time.

    The reader appends .md, so the file placed here has that suffix - otherwise
    the traversal fails for the wrong reason and the test proves nothing.
    """
    # One level up from the knowledge directory, which is where "../" lands.
    outside = knowledge_path(hass).parent / "private.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("the spare key is under the mat")
    knowledge_path(hass).mkdir(parents=True, exist_ok=True)

    try:
        rendered = await async_render(hass, ["../private"])
        assert "spare key" not in rendered
        assert rendered == ""
    finally:
        outside.unlink()


async def test_an_empty_file_adds_no_heading(hass: HomeAssistant) -> None:
    write(hass, "blank.md", "   \n\n  ")
    assert await async_render(hass, ["blank"]) == ""
