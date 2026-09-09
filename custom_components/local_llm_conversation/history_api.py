"""An LLM API giving the model bounded access to recorded data.

Home Assistant's Assist API answers about now. This answers about before: when the
back door last opened, whether the freezer stayed cold, how much electricity went
yesterday.

Everything here is bounded on purpose. A local model has a fixed context window,
so a query returning a month of readings is not an expensive answer, it is a
failed turn. Every tool clamps its range, caps its rows, and says when it has
dropped something rather than quietly returning less than was asked for.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util

from .const import DOMAIN

# A model asking for a year of readings does not want a year of readings; it
# wants the shape of them. These are the ceilings, not the defaults.
MAX_HISTORY_HOURS = 24 * 7
MAX_STATISTICS_DAYS = 365
MAX_ENERGY_DAYS = 92
# Roughly what fits in a reply without crowding out the conversation itself.
MAX_ROWS = 50

API_ID = f"{DOMAIN}.history"
API_PROMPT = """
You can look at recorded data as well as the current state.

Use GetHistory for what a specific thing was doing recently, GetStatistics for
long-run minimums, maximums and averages, and GetEnergy for electricity use.

Results are sampled evenly across the period you ask for, not truncated, so they
show the whole span at lower resolution. When a result says it was sampled, say so
rather than presenting it as every reading.
"""


def _downsample(rows: list[Any]) -> tuple[list[Any], bool]:
    """Thin rows evenly to MAX_ROWS, keeping the first and last.

    Evenly rather than by truncation: the last hour of a week is not an answer
    about the week, and a model given the tail will confidently describe it as
    the whole.
    """
    if len(rows) <= MAX_ROWS:
        return rows, False
    step = (len(rows) - 1) / (MAX_ROWS - 1)
    return [rows[round(i * step)] for i in range(MAX_ROWS)], True


def _require_exposed(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return an error if the model may not look at this entity."""
    if hass.states.get(entity_id) is None:
        return f"There is no entity called {entity_id}."
    if not async_should_expose(hass, conversation.DOMAIN, entity_id):
        # The same boundary the rest of the integration honours: history must
        # not become a way around what a household chose to expose.
        return f"{entity_id} is not exposed to the assistant."
    return None


class GetHistoryTool(llm.Tool):
    """What an entity has been doing."""

    name = "GetHistory"
    description = (
        "Get the recorded state changes of one entity over a recent period. "
        "Use for questions about what something was doing, or when it last changed."
    )
    parameters = vol.Schema(
        {
            vol.Required("entity_id"): str,
            vol.Optional("hours", default=24): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=MAX_HISTORY_HOURS)
            ),
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        """Return sampled state changes."""
        from homeassistant.components.recorder import get_instance, history

        entity_id = tool_input.tool_args["entity_id"]
        if error := _require_exposed(hass, entity_id):
            return {"error": error}

        hours = min(int(tool_input.tool_args.get("hours", 24)), MAX_HISTORY_HOURS)
        start = dt_util.utcnow() - timedelta(hours=hours)

        def query() -> dict[str, list[Any]]:
            return history.get_significant_states(
                hass,
                start,
                None,
                [entity_id],
                minimal_response=True,
                no_attributes=True,
            )

        states = (await get_instance(hass).async_add_executor_job(query)).get(
            entity_id, []
        )
        rows = [
            {
                "at": _when(state),
                "state": state.state if hasattr(state, "state") else state.get("state"),
            }
            for state in states
        ]
        sampled, was_sampled = _downsample(rows)
        return {
            "entity_id": entity_id,
            "hours": hours,
            "changes": sampled,
            "sampled": was_sampled,
            "total_changes": len(rows),
        }


def _when(state: Any) -> str:
    """Return a state's timestamp, whichever shape the recorder returned it in."""
    when = getattr(state, "last_changed", None) or (
        state.get("last_changed") if isinstance(state, dict) else None
    )
    if isinstance(when, datetime):
        return when.isoformat(timespec="seconds")
    return str(when)


class GetStatisticsTool(llm.Tool):
    """Long-run aggregates, which stay small however long the period."""

    name = "GetStatistics"
    description = (
        "Get long-run minimum, maximum, mean and total for one entity, by hour or "
        "by day. Use for questions spanning more than a day or two."
    )
    parameters = vol.Schema(
        {
            vol.Required("entity_id"): str,
            vol.Optional("days", default=7): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=MAX_STATISTICS_DAYS)
            ),
            vol.Optional("period", default="day"): vol.In(["hour", "day"]),
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        """Return aggregated statistics."""
        from homeassistant.components.recorder import get_instance, statistics

        entity_id = tool_input.tool_args["entity_id"]
        if error := _require_exposed(hass, entity_id):
            return {"error": error}

        days = min(int(tool_input.tool_args.get("days", 7)), MAX_STATISTICS_DAYS)
        period = tool_input.tool_args.get("period", "day")
        start = dt_util.utcnow() - timedelta(days=days)

        def query() -> dict[str, list[Any]]:
            return statistics.statistics_during_period(
                hass,
                start,
                None,
                {entity_id},
                period,
                None,
                {"min", "max", "mean", "change"},
            )

        rows = (await get_instance(hass).async_add_executor_job(query)).get(
            entity_id, []
        )
        if not rows:
            return {
                "entity_id": entity_id,
                "error": "No statistics are recorded for this entity.",
            }

        sampled, was_sampled = _downsample(rows)
        return {
            "entity_id": entity_id,
            "days": days,
            "period": period,
            "statistics": [
                {
                    "start": _stat_time(row.get("start")),
                    **{
                        key: round(row[key], 2)
                        for key in ("min", "max", "mean", "change")
                        if isinstance(row.get(key), (int, float))
                    },
                }
                for row in sampled
            ],
            "sampled": was_sampled,
        }


def _stat_time(value: Any) -> str:
    """Statistics rows carry a unix timestamp rather than a datetime."""
    if isinstance(value, (int, float)):
        return dt_util.utc_from_timestamp(value).isoformat(timespec="seconds")
    return str(value)


class GetEnergyTool(llm.Tool):
    """Electricity use, from the energy dashboard's own sources."""

    name = "GetEnergy"
    description = (
        "Get electricity consumption per day, totalled across the sources "
        "configured in the energy dashboard. Use for questions about power or "
        "electricity use."
    )
    parameters = vol.Schema(
        {
            vol.Optional("days", default=7): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=MAX_ENERGY_DAYS)
            )
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        """Return daily consumption."""
        from homeassistant.components.energy.data import async_get_manager
        from homeassistant.components.recorder import get_instance, statistics

        try:
            manager = await async_get_manager(hass)
        except Exception:  # noqa: BLE001 - energy may not be set up at all
            return {"error": "The energy dashboard is not set up."}
        if not manager.data:
            return {"error": "The energy dashboard is not set up."}

        stat_ids = _consumption_statistics(manager.data)
        if not stat_ids:
            return {"error": "No electricity sources are configured."}

        days = min(int(tool_input.tool_args.get("days", 7)), MAX_ENERGY_DAYS)
        start = dt_util.utcnow() - timedelta(days=days)

        def query() -> dict[str, list[Any]]:
            return statistics.statistics_during_period(
                hass, start, None, stat_ids, "day", None, {"change"}
            )

        results = await get_instance(hass).async_add_executor_job(query)

        # Several sources make one household total, which is what was asked.
        per_day: dict[str, float] = {}
        for rows in results.values():
            for row in rows:
                if isinstance(change := row.get("change"), (int, float)):
                    day = _stat_time(row.get("start"))[:10]
                    per_day[day] = per_day.get(day, 0.0) + change

        daily = [
            {"day": day, "consumption": round(total, 2)}
            for day, total in sorted(per_day.items())
        ]
        sampled, was_sampled = _downsample(daily)
        return {
            "days": days,
            "sources": len(stat_ids),
            "daily": sampled,
            "sampled": was_sampled,
            "total": round(sum(per_day.values()), 2),
        }


def _consumption_statistics(data: dict[str, Any]) -> set[str]:
    """Return the statistic ids the energy dashboard counts as consumption."""
    stat_ids: set[str] = set()
    for source in data.get("energy_sources", []):
        for flow in source.get("flow_from", []):
            if stat_id := flow.get("stat_energy_from"):
                stat_ids.add(stat_id)
        if stat_id := source.get("stat_energy_from"):
            stat_ids.add(stat_id)
    return stat_ids


class HistoryAPI(llm.API):
    """Bundles the recorded-data tools as an API a model can be given."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the API."""
        super().__init__(hass=hass, id=API_ID, name="Recorded data (history)")

    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        """Return the tools."""
        return llm.APIInstance(
            api=self,
            api_prompt=API_PROMPT,
            llm_context=llm_context,
            tools=[GetHistoryTool(), GetStatisticsTool(), GetEnergyTool()],
        )
