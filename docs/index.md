---
title: Local LLM Conversation
---

A Home Assistant conversation agent for any OpenAI-compatible endpoint — built for
local serving stacks such as [**vLLM**](https://github.com/vllm-project/vllm), [**SGLang**](https://github.com/sgl-project/sglang), [**LiteLLM**](https://github.com/BerriAI/litellm) and [**fastllm
proxy**](https://github.com/azrtydxb/Fastllm-proxy).

Ask it to turn the lights off, what the freezer did overnight, or who is at the
door. It runs on your own hardware and answers like a household butler rather than
a chatbot.

- [Installation and setup](install.md)
- [Configuration reference](configuration.md)
- [What the agent can do](capabilities.md)
- [How it behaves](behaviour.md)
- [Speed](speed.md)
- [Troubleshooting](troubleshooting.md)

## Why another one

This is not a fork of `extended_openai_conversation`. That project predates Home
Assistant's own LLM support and reimplements entity exposure, tool calling and
conversation history by hand. Home Assistant now provides all three, so this
integration is a thin adapter instead:

- **Tools come from Home Assistant.** The built-in Assist API, any MCP server you
  have configured, and the APIs this integration adds are offered to the model,
  and Home Assistant executes the calls. There is no `execute_service` escape
  hatch, so the model can only touch entities you have actually exposed.
- **History is Home Assistant's.** No unbounded in-memory message dictionaries.
- **Streaming throughout.** Deltas are forwarded as they arrive, so the voice
  pipeline can begin speaking before generation finishes.
- **Reasoning models handled.** Separated reasoning (`reasoning_content` on vLLM
  and SGLang, `reasoning` on fastllm proxy) and inline `<think>…</think>` are all
  routed to Home Assistant's `thinking_content`, so reasoning is recorded but
  never spoken.

## Requirements

Home Assistant **2025.9.0** or newer — that release introduced `thinking_content`
and the streamed text-to-speech handoff this integration relies on.

An OpenAI-compatible endpoint. The `recorder` and `energy` integrations are
optional; without them everything works except the tools that need them, which say
so plainly.
