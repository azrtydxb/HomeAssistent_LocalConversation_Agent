---
title: What the agent can do
---

[Back to the index](index.md)

Capabilities are chosen per model under **Control Home Assistant**. Selecting
several combines them. Without any of them the agent can hold a conversation but
cannot act.

| API                                      | Gives the agent                                              |
| ---------------------------------------- | ------------------------------------------------------------ |
| **Assist** (Home Assistant's own)        | Control of exposed devices, and the live state of the house. |
| **Recorded data (history)**              | What happened before.                                        |
| **Memory (remembers between chats)**     | Keeping things across conversations.                         |
| **Suggest automations (needs approval)** | Drafting an automation for you to approve.                   |
| Any MCP server you have configured       | Whatever that server offers. Nothing to set up here.         |

## Recorded data

Assist answers about now. These three answer about before:

| Tool            | For                                                                |
| --------------- | ------------------------------------------------------------------ |
| `GetHistory`    | What one entity was doing recently, and when it last changed.      |
| `GetStatistics` | Long-run minimum, maximum, mean and total, by hour or day.         |
| `GetEnergy`     | Electricity use per day, from the energy dashboard's grid sources. |

Everything is bounded, deliberately. A local model has a fixed context window, so
a query returning a month of readings is not an expensive answer — it is a failed
turn. History caps at 7 days, statistics at a year, energy at 92 days, and every
result is thinned to 50 rows.

Results are **sampled evenly across the period, not truncated**. The last hour of
a week is not an answer about the week, and a model handed the tail will describe
it confidently as the whole. A thinned result says so.

Entities obey the same exposure rule as everything else: history cannot read what
you did not expose. `GetEnergy` reads only **grid** sources — dashboards commonly
carry water and gas meters too, and adding litres into a kilowatt-hour total gives
a confident, wrong number. When nothing is recorded it says so rather than
reporting zero; no data and no consumption are different answers.

## Memory

The agent decides what to keep, using a `Remember` tool, and can `Forget` on
request. Memories are put into the prompt rather than fetched with a tool, because
a model does not know to ask for something it does not know exists.

They are shared by every agent on every provider — a household has one butler
however many models sit behind it — and capped at 50, because every memory is paid
for in prompt tokens on every turn. The oldest is dropped when that is exceeded.

Nothing is remembered unless you switch this on. Diagnostics report how many
memories are kept, never their content.

## Suggesting automations

The agent can draft an automation. It cannot create one.

Home Assistant validates the draft; a valid one arrives as a notification with the
YAML for you to add, and an invalid one goes back to the model to fix rather than
in front of you.

Everything else the agent does is undone by saying the opposite. An automation
persists, runs unattended, and a misunderstanding can act at three in the morning
for months before anyone notices.

## Vision

Image attachments are passed to the model, so a camera snapshot can be part of the
question.

Whether a model reads images is settled by **testing it when you pick it**, and
the setting is turned on or off from the result. The test is not "does the
endpoint accept an image": [vLLM](https://github.com/vllm-project/vllm) answers `200` to an image sent to a text-only
model and silently drops it, which would report vision on every model. What cannot
be faked is the token count — encoding an image costs prompt tokens, ignoring it
costs none — so the same tiny request is sent with and without an image and the
two are compared. If the endpoint reports no usage the question cannot be settled
and images stay off.

Non-image attachments are skipped, and a snapshot deleted between turns is dropped
rather than failing the conversation.

## AI Task

A provider can also carry **AI Task** models, which generate data for automations
rather than holding a conversation — a shopping list from a photo of the fridge, a
summary of the day. Add one the same way you add a chat model.

When an automation asks for a structure, the schema is sent as `response_format`
with a JSON schema, which vLLM honours. vLLM's own `guided_json` is deliberately
not used: proxies in front of it pass the field through untouched and the model
then answers in prose, which looks like the constraint silently failing. If an
endpoint ignores `response_format` too, the task fails saying the model did not
return the requested structure, rather than handing an automation a paragraph.
