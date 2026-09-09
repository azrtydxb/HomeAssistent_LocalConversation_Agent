---
title: Troubleshooting
---

[Back to the index](index.md)

## The provider will not set up

The endpoint is contacted once at startup.

- **Unreachable** — Home Assistant retries with backoff rather than failing
  outright. Check the URL and that the server is running.
- **Key rejected** — you are asked for a new one rather than being retried
  forever. The same happens if a key is revoked while running.

## An agent shows as unavailable

The endpoint could not be reached on the last turn. Nothing polls a conversation
agent, so this is the only signal it has; it clears on the next successful turn.

## Seeing what the model was actually asked

Turn on debug logging for `custom_components.local_llm_conversation`. The full
request is written to the log — the assembled prompt, the exposed entity list and
the tool definitions.

It contains everything in your house, so it is off unless you ask for it.

```yaml
logger:
  logs:
    custom_components.local_llm_conversation: debug
```

## Diagnostics

Downloadable from the provider's menu. The API key is redacted, and the soul is
reported by length rather than content, since a household may have written
something personal into it. Memories are reported as a count, never as text.

## Every turn fails with an invalid type error

Older versions sent whole numbers as floats, which endpoints expecting an integer
reject. Fixed in 0.11.1 — update.

## The agent answers but never acts

No tool API is selected. Choose at least **Assist** under **Control Home
Assistant**.

If tools are selected and it still does not act, the model may not support tool
calling; try **prompted** under [tool calling](behaviour.md#tool-calling).
