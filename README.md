# Local LLM Conversation

**A Home Assistant conversation agent for any OpenAI-compatible endpoint — built
for local models.**

Point it at [**vLLM**](https://github.com/vllm-project/vllm), [**SGLang**](https://github.com/sgl-project/sglang), [**LiteLLM**](https://github.com/BerriAI/litellm) or [**fastllm proxy**](https://github.com/azrtydxb/Fastllm-proxy) and talk to your
house. Ask it to turn the lights off, what the freezer did overnight, or who is at
the door. It runs on your own hardware and answers like a household butler rather
than a chatbot.

📖 **[Full documentation](https://azrtydxb.github.io/ha-local-llm-conversation)**

## What you get

- **Control your house** through Home Assistant's own Assist API — no separate
  entity exposure, and no way for the model to reach what you have not exposed.
- **Answers about the past**, not just now: history, statistics and energy use.
- **Vision**, so a camera snapshot can be part of the question. Models are tested
  for it when you pick them.
- **Memory** between conversations, and **knowledge files** about your house.
- **Automation drafts** for you to approve — never created behind your back.
- **A persona**, so it sounds like the same assistant every time.
- **Fast.** Streaming throughout, reasoning off by default, and a prompt built to
  stay in the server's prefix cache.

## Requirements

Home Assistant **2025.9.0** or newer, and an OpenAI-compatible endpoint.

## Install

Add this repository to HACS as a custom repository (category: Integration),
restart Home Assistant, then add **Local LLM Conversation** from _Settings →
Devices & services_.

You add a **provider** (your endpoint), then one or more **models** on it. Each
model becomes its own conversation agent.

→ [Installation and setup](https://azrtydxb.github.io/ha-local-llm-conversation/install.html) ·
[Configuration](https://azrtydxb.github.io/ha-local-llm-conversation/configuration.html) ·
[What the agent can do](https://azrtydxb.github.io/ha-local-llm-conversation/capabilities.html) ·
[Troubleshooting](https://azrtydxb.github.io/ha-local-llm-conversation/troubleshooting.html)

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest
```

Tests are expected to fail when the code is broken, which is checked by breaking
it: each non-trivial behaviour has been verified by reverting it and confirming
the suite goes red.

## Licence

MIT.
