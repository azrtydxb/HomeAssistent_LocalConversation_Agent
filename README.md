# Local LLM Conversation

A Home Assistant conversation agent for any OpenAI-compatible endpoint — built for
local serving stacks such as **vLLM**, **SGLang**, **LiteLLM** and **fastllm proxy**.

## Why another one

This is not a fork of `extended_openai_conversation`. That project predates Home
Assistant's own LLM support and reimplements entity exposure, tool calling and
conversation history by hand. Home Assistant now provides all three, so this
integration is a thin adapter instead:

- **Tools come from Home Assistant.** The built-in Assist API and any MCP server
  you have configured are offered to the model, and Home Assistant executes the
  calls. There is no `execute_service` escape hatch, so the model can only touch
  entities you have actually exposed.
- **History is Home Assistant's.** No unbounded in-memory message dictionaries.
- **Streaming throughout.** Deltas are forwarded as they arrive, so the voice
  pipeline can begin speaking before generation finishes.
- **Reasoning models handled.** Separated reasoning (`reasoning_content` on vLLM
  and SGLang, `reasoning` on fastllm proxy) and inline `<think>…</think>` are all
  routed to Home Assistant's `thinking_content` field, so reasoning is recorded
  but never spoken.

## Requirements

Home Assistant **2025.9.0** or newer — that release introduced `thinking_content`
and the streamed text-to-speech handoff this integration relies on.

## Installation

Add this repository to HACS as a custom repository (category: Integration),
install, restart Home Assistant, then add **Local LLM Conversation** from
_Settings → Devices & services_.

Setup has two tiers. First you add a **provider** — one OpenAI-compatible
endpoint. `http://192.168.1.10:8000` is enough; the `/v1` suffix is added if
missing.

Then you add one or more **models** to that provider, each becoming its own
conversation agent. Models are offered in a dropdown built from what the endpoint
serves; if the server has no `/v1/models` listing, or routes a name it does not
advertise, you can type one instead. Running a fast model for voice alongside a
larger one for harder questions is a single provider with two models.

To use it, assign the agent to a voice assistant in
_Settings → Voice assistants_.

## Configuration

| Option                               | Notes                                                                                                                      |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| Model                                | Defaults to the endpoint's only model.                                                                                     |
| Instructions                         | Replaces Home Assistant's default system prompt.                                                                           |
| Control Home Assistant               | Which tool APIs the model may use. Without one it can talk but not act.                                                    |
| Let the model think first            | Off by default. See below.                                                                                                 |
| Maximum tokens / Temperature / Top P | Passed through to the endpoint. Reasoning models spend a large part of this budget before answering, so do not set it low. |

### Language

By default the agent answers in whatever language it was addressed in. Set
**Always answer in** and it replies in that language whatever you use.

The instruction carries only the IETF language tag; no table of language names is
maintained. Verified against a local Qwen3: asked in English with the tag `nl`, it
answers in Dutch.

Your voice assistant's text-to-speech voice has its own language setting, which
this cannot reach. If the two disagree you get correct text read in the wrong
accent.

### Sampling

Temperature and Top P are sent only if you change them. Left alone, they are
omitted and the endpoint's own defaults apply — some providers reject a request
that sets both, and Anthropic is one of them.

### Thinking

Reasoning models produce a think block before answering. It is switched off by
default, because it costs seconds on every turn and a large share of the token
budget — a one-line question was measured spending its entire budget reasoning and
returning no answer at all. Home Assistant is asked mostly to turn lights on.

Switching it off sends `chat_template_kwargs: {"enable_thinking": false}`, which
is how vLLM and SGLang disable it. Endpoints that ignore the hint simply keep
reasoning, and the reasoning is still kept out of the spoken reply.
| Response timeout | Time to wait between streamed tokens before giving up. |
| How the model calls tools | Native, prompted, or none. See below. |

## Notes on performance

vLLM and SGLang cache the prompt prefix between requests, which matters a lot
once a house full of exposed entities makes the system prompt large. Home
Assistant helps here: it places the volatile date and time _after_ the entity
list rather than before it, and omits it entirely when the selected API exposes a
`GetDateTime` tool. This integration adds nothing volatile of its own, so the
prefix stays stable across turns and prefill stays cheap.

### Vision

Image attachments are passed through to the model, so a camera snapshot can be
part of the question — "who is at the front door?".

Whether a model reads images is settled by testing it when you pick it, and the
setting is turned on or off from the result. You can still turn it off afterwards
under Advanced.

The test is not "does the endpoint accept an image": vLLM answers `200` to an
image sent to a text-only model and silently drops it, so that would report vision
on every model. What cannot be faked is the token count — encoding an image costs
prompt tokens, ignoring it costs none — so the same tiny request is sent with and
without an image and the two are compared. If the endpoint reports no token usage
the question cannot be settled, and images stay off rather than being sent to a
model that may ignore them.

Non-image attachments are skipped, and a snapshot deleted between turns is dropped
rather than failing the conversation.

### Tool calling

**Native** is the default and right for vLLM, SGLang, LiteLLM and fastllm proxy:
the tools go in the request and the endpoint handles them.

**Prompted** is for small models whose serving stack offers no tool calling at
all. The tools are described in the prompt and the reply is read for a JSON call.
It is less reliable, and the reply cannot begin being spoken until it is complete,
because whether it is an answer or a tool call is not known until then — so voice
responses feel slower. Only a reply that is _nothing but_ a call counts; a model
describing a tool mid-sentence is answering, not calling.

**None** lets the agent talk without acting.

## Recorded data

Home Assistant's Assist API answers about now. Selecting **Recorded data
(history)** under Control Home Assistant as well adds three tools that answer
about before:

| Tool            | For                                                             |
| --------------- | --------------------------------------------------------------- |
| `GetHistory`    | What one entity was doing recently, and when it last changed.   |
| `GetStatistics` | Long-run minimum, maximum, mean and total, by hour or day.      |
| `GetEnergy`     | Electricity use per day, across the energy dashboard's sources. |

Everything is bounded, and deliberately so. A local model has a fixed context
window, so a query returning a month of readings is not an expensive answer — it
is a failed turn. History is capped at 7 days, statistics at a year, energy at 92
days, and every result is thinned to 50 rows.

Results are **sampled evenly across the period, not truncated**. The last hour of
a week is not an answer about the week, and a model handed the tail will describe
it confidently as the whole. When a result has been thinned it says so, so the
agent can tell you it is looking at a sample.

Entities are subject to the same exposure rule as everything else: history cannot
be used to read something you did not expose.

`GetEnergy` reads only the **grid** sources in the energy dashboard. Dashboards
commonly carry water and gas meters too, whose readings are litres and cubic
metres; adding those into a kilowatt-hour total gives a confident, wrong number.
When nothing is recorded for the period it says so rather than reporting zero —
no data and no consumption are different answers, and a model told zero will say
zero.

The recorder and energy integrations are optional. Without them the agent still
works and the tools say plainly what is missing.

## What it knows about this house

The soul says who the agent is. Knowledge files say what it knows about your
particular house: how the heating is zoned, what an oddly named sensor means, the
rules for the holiday cottage.

Put markdown files in `config/local_llm_conversation/knowledge/` and enable them
per agent under Advanced. The picker only appears once you have written something.

Every enabled file is added to the prompt on **every** request, including "turn on
the kitchen light", and prompt size is what drives time to first token — so enable
only what earns it. Files over 32 KB are skipped for the same reason.

This is deliberately just files and a picker. There is no download service and no
registry: if editing the soul covers what you need, use the soul.

## Memory

Selecting **Memory (remembers between chats)** lets the agent keep things across
conversations — who lives here, a preference, a decision. It decides what to keep,
using a `Remember` tool, and can `Forget` on request.

Memories are put into the prompt rather than fetched with a tool, because a model
does not know to ask for something it does not know exists. They are shared by
every agent on every provider: a household has one butler however many models sit
behind it. They are capped at 50 — every memory is paid for in prompt tokens on
every turn — and the oldest is dropped when that is exceeded.

Nothing is remembered unless you switch this on. Diagnostics report how many
memories are kept, never their content.

## Suggesting automations

Selecting **Suggest automations (needs approval)** lets the agent draft one. It
cannot create one.

The draft is validated by Home Assistant and then raised as a notification for you
to read and add. A draft that does not validate goes back to the model to fix
rather than in front of you. Everything else the agent does is undone by saying
the opposite; an automation persists, runs unattended, and a misunderstanding can
act at three in the morning for months before anyone notices.

## AI Task

A provider can also carry **AI Task** models, which generate data for automations
rather than holding a conversation — a shopping list from a photo of the fridge, a
summary of the day. Add one the same way you add a chat model.

When an automation asks for a structure, the schema is sent as `response_format`
with a JSON schema, which vLLM honours. vLLM's own `guided_json` is deliberately
not used: proxies in front of it pass the field through untouched and the model
then answers in prose, which looks like the constraint silently failing. If an
endpoint ignores `response_format` too, the task fails with a message saying the
model did not return the requested structure, rather than handing an automation
a paragraph where it expected fields.

## When things go wrong

The provider is contacted once at startup. If it cannot be reached, Home
Assistant retries with backoff rather than failing outright; if it rejects the
key, you are asked for a new one instead of being retried forever. An agent whose
endpoint has gone away reports itself unavailable rather than waiting for someone
to speak to it.

Turning on debug logging for `custom_components.local_llm_conversation` writes the
full request — the assembled prompt, the exposed entity list and the tool
definitions — to the log, which is the only way to see what the model was actually
asked. It contains everything in your house, so it is off unless you ask for it.

Diagnostics can be downloaded from the provider's menu for bug reports. The API
key is redacted, and the soul is reported by length rather than content, since a
household may have written something personal into it.

## Translations

The configuration UI is available in English, Dutch, German and French.

The translations are mine and have not been checked by a native speaker.
Corrections are welcome, as are new languages: `strings.json` is the source, and a
translation is one file under `translations/` with the same structure. Tests check
that every string is covered and that no `{placeholder}` has been dropped or
renamed, so a partial file fails rather than showing raw keys in the UI.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest
```
