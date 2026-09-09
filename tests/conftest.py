"""Fixtures for the Local LLM Conversation tests."""

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of the custom integration in every test."""
    return


@pytest.fixture(autouse=True)
def clean_knowledge_directory(hass):
    """Start every test with no knowledge files.

    The config directory is shared across the test session, so a file written by
    one test otherwise changes the form another test renders.
    """
    from custom_components.local_llm_conversation.knowledge import knowledge_path

    directory = knowledge_path(hass)
    if directory.is_dir():
        for path in directory.iterdir():
            if path.is_file():
                path.unlink()
    return directory
