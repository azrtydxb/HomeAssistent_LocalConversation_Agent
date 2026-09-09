---
title: Speed
---

[Back to the index](index.md)

Prefill dominates. Measured against [vLLM](https://github.com/vllm-project/vllm) with a warm cache:

| System prompt | Time to first token |
| ------------- | ------------------- |
| 768 tokens    | 0.34s               |
| 3,111 tokens  | 0.60s               |
| 9,428 tokens  | 0.83s               |

vLLM and [SGLang](https://github.com/sgl-project/sglang) cache the prompt prefix between requests, and that cache is worth
roughly **3×**: the same 9k-token prompt answers in 0.83s when the prefix is
stable and 2.7s when it changes each turn. Shuffling the tool order costs the
same, on every turn rather than just the first.

Nothing here changes between turns, so the cache holds. Home Assistant helps: it
places the volatile date and time _after_ the entity list rather than before it,
and omits it entirely when the selected API exposes a `GetDateTime` tool.

## What you can change

**Turn thinking off.** Already the default, and worth 5.3s → 0.3s on a reasoning
model.

**Expose fewer entities.** This is the lever left to you. Every exposed entity is
paid for on every utterance, and the difference between a 768-token prompt and a
9,428-token one is roughly half a second on each one.

**Enable only the knowledge files and capabilities you use.** Each is added to the
prompt whether or not the question needs it.
