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

Point it at your endpoint's base URL. `http://192.168.1.10:8000` is enough — the
`/v1` suffix is added if missing. If the endpoint serves exactly one model it is
selected automatically.

To use it, assign the agent to a voice assistant in
_Settings → Voice assistants_.

## Configuration

| Option                               | Notes                                                                                                                      |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| Model                                | Defaults to the endpoint's only model.                                                                                     |
| Instructions                         | Replaces Home Assistant's default system prompt.                                                                           |
| Control Home Assistant               | Which tool APIs the model may use. Without one it can talk but not act.                                                    |
| Maximum tokens / Temperature / Top P | Passed through to the endpoint. Reasoning models spend a large part of this budget before answering, so do not set it low. |
| Response timeout                     | Time to wait between streamed tokens before giving up.                                                                     |
| Endpoint supports tool calling       | Turn off only for models that cannot call tools.                                                                           |

## Notes on performance

vLLM and SGLang cache the prompt prefix between requests, which matters a lot
once a house full of exposed entities makes the system prompt large. Home
Assistant helps here: it places the volatile date and time _after_ the entity
list rather than before it, and omits it entirely when the selected API exposes a
`GetDateTime` tool. This integration adds nothing volatile of its own, so the
prefix stays stable across turns and prefill stays cheap.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest
```
