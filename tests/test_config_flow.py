"""Tests for the provider config flow and the model subentry flow."""

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, section
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.local_llm_conversation.const import (
    CONF_ADVANCED,
    CONF_ASSISTANT_NAME,
    CONF_BASE_URL,
    CONF_MODEL,
    CONF_PROMPT,
    CONF_SUPPORTS_TOOLS,
    CONF_VISION,
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_SOUL,
    DOMAIN,
    SUBENTRY_TYPE_CONVERSATION,
)

MODELS_URL = "http://host:8000/v1/models"


@pytest.fixture(autouse=True)
async def setup_dependencies(hass: HomeAssistant) -> None:
    """conversation depends on the homeassistant component for entity exposure."""
    assert await async_setup_component(hass, "homeassistant", {})


def two_models(mock: AiohttpClientMocker) -> None:
    mock.get(MODELS_URL, json={"data": [{"id": "qwen3-32b"}, {"id": "llama-3.3-70b"}]})


async def add_provider(hass: HomeAssistant, url: str = "http://host:8000"):
    """Run the provider flow to completion and return the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: url, CONF_API_KEY: "sk-x"}
    )
    await hass.async_block_till_done()
    return result


async def start_reconfigure(hass: HomeAssistant, entry):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )


async def open_model_form(hass: HomeAssistant, entry):
    """Open the model picker, the first step of adding a model."""
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CONVERSATION),
        context={"source": config_entries.SOURCE_USER},
    )


async def choose_model(hass: HomeAssistant, entry, model="qwen3-32b", name="Voice"):
    """Pick a model and advance to the settings step, probing on the way."""
    result = await open_model_form(hass, entry)
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_NAME: name, CONF_MODEL: model}
    )


def probing(detected):
    """Patch the vision probe, which runs when a model is chosen."""
    return patch(
        "custom_components.local_llm_conversation.client.ChatCompletionsClient"
        ".async_probe_vision",
        return_value=detected,
    )


# --- provider tier ----------------------------------------------------------


async def test_provider_asks_only_for_the_connection(hass: HomeAssistant) -> None:
    """A provider is an endpoint; models are chosen per subentry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"
    assert set(result["data_schema"].schema) == {CONF_BASE_URL, CONF_API_KEY}


async def test_provider_is_created_without_a_model(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    two_models(aioclient_mock)
    result = await add_provider(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "http://host:8000"
    assert CONF_MODEL not in result["data"]


async def test_the_same_provider_cannot_be_added_twice(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    two_models(aioclient_mock)
    await add_provider(hass)
    result = await add_provider(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_unreachable_provider_reports_on_the_url_field(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(MODELS_URL, exc=TimeoutError)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: "http://host:8000"}
    )
    assert result["errors"] == {CONF_BASE_URL: "cannot_connect"}


async def test_rejected_key_reports_on_the_key_field(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(MODELS_URL, status=401)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: "http://host:8000", CONF_API_KEY: "wrong"}
    )
    assert result["errors"] == {CONF_API_KEY: "invalid_auth"}


# --- model tier -------------------------------------------------------------


async def test_everyday_settings_are_visible_and_the_rest_folded_away(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Most people never touch temperature or the persona."""
    two_models(aioclient_mock)
    entry = (await add_provider(hass))["result"]
    with probing(True):
        result = await choose_model(hass, entry)

    schema = result["data_schema"].schema
    visible = [str(key) for key in schema if not isinstance(schema[key], section)]
    assert visible == [CONF_LLM_HASS_API, CONF_ASSISTANT_NAME]

    advanced = schema[CONF_ADVANCED]
    assert isinstance(advanced, section)
    assert advanced.options["collapsed"] is True
    assert CONF_PROMPT in advanced.schema.schema
    assert CONF_SUPPORTS_TOOLS in advanced.schema.schema


async def test_models_come_from_the_provider(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    two_models(aioclient_mock)
    entry = (await add_provider(hass))["result"]
    result = await open_model_form(hass, entry)

    selector = result["data_schema"].schema[CONF_MODEL]
    assert [option["value"] for option in selector.config["options"]] == [
        "llama-3.3-70b",
        "qwen3-32b",
    ]
    assert selector.config["custom_value"] is True


async def test_the_soul_is_prefilled_and_the_name_defaults_to_jarvis(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    two_models(aioclient_mock)
    entry = (await add_provider(hass))["result"]
    with probing(True):
        result = await choose_model(hass, entry)

    schema = result["data_schema"].schema
    name_key = next(key for key in schema if str(key) == CONF_ASSISTANT_NAME)
    assert name_key.default() == DEFAULT_ASSISTANT_NAME

    advanced = schema[CONF_ADVANCED].schema.schema
    prompt_key = next(key for key in advanced if str(key) == CONF_PROMPT)
    assert prompt_key.description["suggested_value"] == DEFAULT_SOUL


async def test_adding_a_model_creates_a_subentry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    two_models(aioclient_mock)
    entry = (await add_provider(hass))["result"]
    with probing(True):
        result = await choose_model(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_LLM_HASS_API: ["assist"],
            CONF_ASSISTANT_NAME: "Jarvis",
            CONF_ADVANCED: {},
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Voice"
    subentry = next(iter(entry.subentries.values()))
    assert subentry.data[CONF_MODEL] == "qwen3-32b"
    assert subentry.data[CONF_ASSISTANT_NAME] == "Jarvis"


async def test_two_models_on_one_provider_each_get_an_agent(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A fast model for voice and a larger one for hard questions."""
    two_models(aioclient_mock)
    entry = (await add_provider(hass))["result"]

    for name, model in (("Voice", "qwen3-32b"), ("Study", "llama-3.3-70b")):
        with probing(True):
            result = await choose_model(hass, entry, model=model, name=name)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_LLM_HASS_API: ["assist"],
                CONF_ASSISTANT_NAME: "Jarvis",
                CONF_ADVANCED: {},
            },
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert len(entry.subentries) == 2
    agents = [
        state.entity_id
        for state in hass.states.async_all()
        if state.entity_id.startswith("conversation.")
    ]
    assert len(agents) == 3  # two of ours plus Home Assistant's own


async def test_the_provider_url_and_key_can_be_changed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without this there is no way to fix a moved endpoint or a rotated key."""
    two_models(aioclient_mock)
    entry = (await add_provider(hass))["result"]
    aioclient_mock.get("http://newhost:9000/v1/models", json={"data": []})

    result = await start_reconfigure(hass, entry)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_BASE_URL: "http://newhost:9000", CONF_API_KEY: "sk-new"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_BASE_URL] == "http://newhost:9000"
    assert entry.data[CONF_API_KEY] == "sk-new"
    assert entry.title == "http://newhost:9000"


async def test_reconfigure_reports_a_bad_endpoint_instead_of_saving_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    two_models(aioclient_mock)
    entry = (await add_provider(hass))["result"]
    aioclient_mock.get("http://dead:9000/v1/models", exc=TimeoutError)

    result = await start_reconfigure(hass, entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: "http://dead:9000"}
    )

    assert result["errors"] == {CONF_BASE_URL: "cannot_connect"}
    assert entry.data[CONF_BASE_URL] == "http://host:8000"


async def test_reconfigure_refuses_to_collide_with_another_provider(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Two providers on one URL would fight over the same models."""
    two_models(aioclient_mock)
    aioclient_mock.get("http://other:8000/v1/models", json={"data": []})
    first = (await add_provider(hass))["result"]
    await add_provider(hass, "http://other:8000")

    result = await start_reconfigure(hass, first)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: "http://other:8000"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert first.data[CONF_BASE_URL] == "http://host:8000"


async def test_the_api_key_is_masked(hass: HomeAssistant) -> None:
    """The key is a secret and should not sit in plain sight on screen."""
    from homeassistant.helpers.selector import TextSelector

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    selector = result["data_schema"].schema[CONF_API_KEY]
    assert isinstance(selector, TextSelector)
    assert selector.config["type"] == "password"


async def test_the_model_is_probed_when_it_is_chosen(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The result is shown while configuring, not discovered later in use."""
    two_models(aioclient_mock)
    entry = (await add_provider(hass))["result"]
    with probing(True):
        result = await choose_model(hass, entry)

    assert result["step_id"] == "settings"
    assert result["description_placeholders"] == {
        "model": "qwen3-32b",
        "vision": "yes, this model reads images",
    }
    advanced = result["data_schema"].schema[CONF_ADVANCED].schema.schema
    vision = next(key for key in advanced if str(key) == CONF_VISION)
    assert vision.default() is True


async def test_a_model_that_ignores_images_gets_vision_turned_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    two_models(aioclient_mock)
    entry = (await add_provider(hass))["result"]
    with probing(False):
        result = await choose_model(hass, entry)

    assert result["description_placeholders"]["vision"] == (
        "no, this model ignores images"
    )
    advanced = result["data_schema"].schema[CONF_ADVANCED].schema.schema
    vision = next(key for key in advanced if str(key) == CONF_VISION)
    assert vision.default() is False


async def test_the_user_can_turn_vision_off_on_a_model_that_supports_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Detection sets the default; the household still decides."""
    two_models(aioclient_mock)
    entry = (await add_provider(hass))["result"]
    with probing(True):
        result = await choose_model(hass, entry)
    await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_LLM_HASS_API: ["assist"],
            CONF_ASSISTANT_NAME: "Jarvis",
            CONF_ADVANCED: {CONF_VISION: False},
        },
    )
    await hass.async_block_till_done()

    subentry = next(iter(entry.subentries.values()))
    assert subentry.data[CONF_ADVANCED][CONF_VISION] is False


async def test_switching_to_a_model_without_vision_turns_it_off_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Vision on from a previous model must not carry to one that cannot see."""
    two_models(aioclient_mock)
    entry = (await add_provider(hass))["result"]
    with probing(True):
        result = await choose_model(hass, entry)
    await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_LLM_HASS_API: ["assist"],
            CONF_ASSISTANT_NAME: "Jarvis",
            CONF_ADVANCED: {CONF_VISION: True},
        },
    )
    await hass.async_block_till_done()

    subentry = next(iter(entry.subentries.values()))
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CONVERSATION),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry.subentry_id,
        },
    )
    with probing(False):
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_MODEL: "llama-3.3-70b"}
        )

    advanced = result["data_schema"].schema[CONF_ADVANCED].schema.schema
    vision = next(key for key in advanced if str(key) == CONF_VISION)
    assert vision.default() is False


async def test_an_undecidable_probe_leaves_images_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Sending images a model may ignore wastes tokens for nothing."""
    two_models(aioclient_mock)
    entry = (await add_provider(hass))["result"]
    with probing(None):
        result = await choose_model(hass, entry)

    assert "could not be determined" in result["description_placeholders"]["vision"]
    advanced = result["data_schema"].schema[CONF_ADVANCED].schema.schema
    vision = next(key for key in advanced if str(key) == CONF_VISION)
    assert vision.default() is False


async def test_a_task_model_is_not_asked_for_a_persona(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A task worker generates data; it has no one to speak to."""
    from custom_components.local_llm_conversation.const import SUBENTRY_TYPE_AI_TASK

    two_models(aioclient_mock)
    entry = (await add_provider(hass))["result"]

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_AI_TASK),
        context={"source": config_entries.SOURCE_USER},
    )
    with probing(True):
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_NAME: "Tasks", CONF_MODEL: "qwen3-32b"}
        )

    schema = result["data_schema"].schema
    assert CONF_ASSISTANT_NAME not in schema
    assert CONF_LLM_HASS_API not in schema
    advanced = schema[CONF_ADVANCED].schema.schema
    assert CONF_PROMPT not in advanced
    assert CONF_SUPPORTS_TOOLS not in advanced
    # The settings that do apply to a task worker are still there.
    assert {str(key) for key in advanced} >= {CONF_VISION, "max_tokens", "temperature"}


async def test_a_task_model_can_be_added_to_a_provider(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    from custom_components.local_llm_conversation.const import SUBENTRY_TYPE_AI_TASK

    two_models(aioclient_mock)
    entry = (await add_provider(hass))["result"]
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_AI_TASK),
        context={"source": config_entries.SOURCE_USER},
    )
    with probing(True):
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_NAME: "Tasks", CONF_MODEL: "qwen3-32b"}
        )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_ADVANCED: {}}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(iter(entry.subentries.values()))
    assert subentry.subentry_type == SUBENTRY_TYPE_AI_TASK
    assert subentry.data[CONF_MODEL] == "qwen3-32b"
