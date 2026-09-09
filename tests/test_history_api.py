"""Tests for the recorded-data tools."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component

from custom_components.local_llm_conversation.history_api import (
    MAX_ROWS,
    GetEnergyTool,
    GetHistoryTool,
    GetStatisticsTool,
    _consumption_statistics,
    _downsample,
    _require_exposed,
)


@pytest.fixture(autouse=True)
async def setup_dependencies(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "homeassistant", {})


def call(tool, **args):
    return llm.ToolInput(tool_name=tool.name, tool_args=args)


def context() -> llm.LLMContext:
    return llm.LLMContext(
        platform="local_llm_conversation",
        context=None,
        language="en",
        assistant="conversation",
        device_id=None,
    )


# --- bounding, which is the whole point ------------------------------------


def test_a_short_result_is_returned_whole() -> None:
    rows = list(range(10))
    assert _downsample(rows) == (rows, False)


def test_a_long_result_is_thinned_not_truncated() -> None:
    """The last hour of a week is not an answer about the week.

    A model handed the tail will describe it confidently as the whole period, so
    the span has to survive even when the resolution does not.
    """
    rows = list(range(500))
    sampled, was_sampled = _downsample(rows)

    assert was_sampled is True
    assert len(sampled) == MAX_ROWS
    assert sampled[0] == 0
    assert sampled[-1] == 499
    # Evenly spread, so the middle of the period is still represented.
    assert 200 < sampled[MAX_ROWS // 2] < 300


def test_exactly_the_cap_is_not_flagged_as_sampled() -> None:
    rows = list(range(MAX_ROWS))
    assert _downsample(rows) == (rows, False)


# --- what the model is allowed to look at ----------------------------------


async def test_an_unexposed_entity_is_refused(hass: HomeAssistant) -> None:
    """History must not become a way around what a household chose to expose."""
    hass.states.async_set("sensor.private", "42")
    with patch(
        "custom_components.local_llm_conversation.history_api.async_should_expose",
        return_value=False,
    ):
        assert "not exposed" in _require_exposed(hass, "sensor.private")


async def test_an_unknown_entity_says_so(hass: HomeAssistant) -> None:
    assert "no entity" in _require_exposed(hass, "sensor.does_not_exist")


async def test_an_exposed_entity_is_allowed(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.kitchen", "21")
    with patch(
        "custom_components.local_llm_conversation.history_api.async_should_expose",
        return_value=True,
    ):
        assert _require_exposed(hass, "sensor.kitchen") is None


async def test_history_refuses_an_unexposed_entity(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.private", "42")
    with patch(
        "custom_components.local_llm_conversation.history_api.async_should_expose",
        return_value=False,
    ):
        result = await GetHistoryTool().async_call(
            hass, call(GetHistoryTool, entity_id="sensor.private"), context()
        )
    assert "not exposed" in result["error"]


# --- the tools themselves ---------------------------------------------------


class FakeState:
    def __init__(self, state: str, when: datetime) -> None:
        self.state = state
        self.last_changed = when


async def test_history_returns_sampled_changes(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.door", "off")
    base = datetime(2026, 9, 1, 12, 0)
    states = {
        "binary_sensor.door": [
            FakeState("on" if i % 2 else "off", base + timedelta(minutes=i))
            for i in range(200)
        ]
    }

    with (
        patch(
            "custom_components.local_llm_conversation.history_api.async_should_expose",
            return_value=True,
        ),
        patch(
            "homeassistant.components.recorder.history.get_significant_states",
            return_value=states,
        ),
        patch(
            "homeassistant.components.recorder.get_instance",
            return_value=_ExecutorStub(hass),
        ),
    ):
        result = await GetHistoryTool().async_call(
            hass,
            call(GetHistoryTool, entity_id="binary_sensor.door", hours=48),
            context(),
        )

    assert result["entity_id"] == "binary_sensor.door"
    assert result["hours"] == 48
    assert result["total_changes"] == 200
    assert result["sampled"] is True
    assert len(result["changes"]) == MAX_ROWS
    assert result["changes"][0]["state"] == "off"
    assert "at" in result["changes"][0]


class _ExecutorStub:
    """Stands in for the recorder instance, running the query inline."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_add_executor_job(self, func, *args):
        return func(*args)


async def test_statistics_says_so_when_none_are_recorded(hass: HomeAssistant) -> None:
    """Most entities have no statistics; that is not a failure to hide."""
    hass.states.async_set("sensor.kitchen", "21")
    with (
        patch(
            "custom_components.local_llm_conversation.history_api.async_should_expose",
            return_value=True,
        ),
        patch(
            "homeassistant.components.recorder.statistics.statistics_during_period",
            return_value={},
        ),
        patch(
            "homeassistant.components.recorder.get_instance",
            return_value=_ExecutorStub(hass),
        ),
    ):
        result = await GetStatisticsTool().async_call(
            hass, call(GetStatisticsTool, entity_id="sensor.kitchen"), context()
        )
    assert "No statistics" in result["error"]


async def test_energy_without_a_dashboard_says_so(hass: HomeAssistant) -> None:
    """Plenty of households never set the energy dashboard up."""
    result = await GetEnergyTool().async_call(hass, call(GetEnergyTool), context())
    assert "energy dashboard" in result["error"]


def test_consumption_sources_are_read_from_the_dashboard() -> None:
    """Only what the household configured as consumption, nothing inferred."""
    data = {
        "energy_sources": [
            {
                "type": "grid",
                "flow_from": [
                    {"stat_energy_from": "sensor.grid_import"},
                    {"stat_energy_from": "sensor.grid_import_2"},
                ],
            },
            {"type": "gas", "stat_energy_from": "sensor.gas"},
            {"type": "solar", "stat_energy_from": "sensor.solar"},
        ]
    }
    assert _consumption_statistics(data) == {
        "sensor.grid_import",
        "sensor.grid_import_2",
        "sensor.gas",
        "sensor.solar",
    }


def test_no_configured_sources_is_an_empty_set() -> None:
    assert _consumption_statistics({}) == set()


async def test_the_api_is_offered_alongside_assist(hass: HomeAssistant) -> None:
    """It has to be selectable, or none of the above is reachable."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.local_llm_conversation.const import (
        CONF_BASE_URL,
        DOMAIN,
        SUBENTRY_TYPE_CONVERSATION,
    )
    from custom_components.local_llm_conversation.history_api import API_ID

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_BASE_URL: "http://localhost:8000"},
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                "title": "Voice",
                "unique_id": None,
                "data": {"model": "qwen3"},
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    offered = {api.id for api in llm.async_get_apis(hass)}
    assert "assist" in offered
    assert API_ID in offered


async def test_the_tools_carry_their_limits_in_the_schema(hass: HomeAssistant) -> None:
    """A model asking for a year of readings must be refused by the schema.

    Clamping only inside the call would let a request through that the endpoint
    then has to survive; rejecting it at the boundary is cheaper and clearer.
    """
    import voluptuous as vol

    from custom_components.local_llm_conversation.history_api import (
        MAX_ENERGY_DAYS,
        MAX_HISTORY_HOURS,
        MAX_STATISTICS_DAYS,
    )

    with pytest.raises(vol.Invalid):
        GetHistoryTool.parameters(
            {"entity_id": "sensor.x", "hours": MAX_HISTORY_HOURS + 1}
        )
    with pytest.raises(vol.Invalid):
        GetStatisticsTool.parameters(
            {"entity_id": "sensor.x", "days": MAX_STATISTICS_DAYS + 1}
        )
    with pytest.raises(vol.Invalid):
        GetEnergyTool.parameters({"days": MAX_ENERGY_DAYS + 1})

    # And the ceiling itself is accepted.
    assert GetHistoryTool.parameters(
        {"entity_id": "sensor.x", "hours": MAX_HISTORY_HOURS}
    )
