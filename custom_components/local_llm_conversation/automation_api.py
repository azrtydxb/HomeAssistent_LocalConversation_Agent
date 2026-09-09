"""A tool that drafts an automation for a person to approve.

Everything else the agent does is undone by saying the opposite. An automation is
different: it persists, it runs unattended, and a model that has misunderstood
"when nobody is home" can act at three in the morning for months before anyone
notices. It also writes to configuration rather than to state.

So nothing is created. The agent drafts, Home Assistant validates the draft, and
the result arrives as a notification for a person to read and add. A draft that
does not validate is returned to the model to fix rather than shown to anyone.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util.yaml import dumper

from .const import DOMAIN, LOGGER

API_ID = f"{DOMAIN}.automation"
API_PROMPT = """
You can draft an automation with ProposeAutomation. You cannot create one.

The draft is shown to a person to approve, so say that you have suggested it, not
that you have set it up. Use the entity ids you have been given, and prefer one
automation that does one thing.
"""


class ProposeAutomationTool(llm.Tool):
    """Draft an automation, for a person to accept."""

    name = "ProposeAutomation"
    description = (
        "Draft an automation and show it to the household for approval. It is not "
        "created and does not run until a person adds it. Use when asked to make "
        "something happen automatically or on a schedule."
    )
    parameters = vol.Schema(
        {
            vol.Required("alias"): str,
            vol.Required("triggers"): list,
            vol.Optional("conditions"): list,
            vol.Required("actions"): list,
            vol.Optional("description"): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        """Validate a draft and put it in front of a person."""
        from homeassistant.components.automation.config import (
            async_validate_config_item,
        )

        args = tool_input.tool_args
        config: dict[str, Any] = {
            "alias": args["alias"],
            "triggers": args["triggers"],
            "actions": args["actions"],
        }
        if conditions := args.get("conditions"):
            config["conditions"] = conditions
        if description := args.get("description"):
            config["description"] = description

        try:
            validated = await async_validate_config_item(hass, "automation", config)
        except Exception as err:  # noqa: BLE001 - the validators raise widely
            # Handed back to the model, not to the household: a draft that does
            # not validate is a draft, not a proposal.
            LOGGER.debug("Automation draft rejected: %s", err)
            return {"error": f"That automation is not valid: {err}"}

        if validated is None:
            return {"error": "That automation is not valid."}

        yaml_text = dumper.dump([config])
        persistent_notification.async_create(
            hass,
            (
                f"The assistant suggests this automation. Nothing has been "
                f"created. To use it, add it under Settings then Automations, or "
                f"paste it into `automations.yaml`:\n\n```yaml\n{yaml_text}```"
            ),
            title=f"Suggested automation: {args['alias']}",
            notification_id=f"{DOMAIN}_automation_{args['alias']}",
        )
        return {
            "proposed": args["alias"],
            "created": False,
            "note": (
                "The draft has been sent to the household for approval. It is not "
                "running. Say that it has been suggested, not set up."
            ),
        }


class AutomationAPI(llm.API):
    """Drafting automations, without creating them."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the API."""
        super().__init__(
            hass=hass, id=API_ID, name="Suggest automations (needs approval)"
        )

    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        """Return the tool."""
        return llm.APIInstance(
            api=self,
            api_prompt=API_PROMPT,
            llm_context=llm_context,
            tools=[ProposeAutomationTool()],
        )
