---
title: Installation and setup
---

[Back to the index](index.md)

Add the repository to HACS as a custom repository (category: Integration),
install, restart Home Assistant, then add **Local LLM Conversation** from
_Settings → Devices & services_.

Setup has two tiers.

## 1. Add a provider

One OpenAI-compatible endpoint. `http://192.168.1.10:8000` is enough; the `/v1`
suffix is added if missing.

The endpoint is contacted straight away, so a wrong URL or a rejected key is
reported on the field that caused it rather than failing silently later.

To change the endpoint or key afterwards, use **Reconfigure** on the provider. The
models under it are kept.

## 2. Add a model

Models are offered in a dropdown built from what the endpoint serves. If it
exposes no `/v1/models` listing, or routes a name it does not advertise, type one
instead.

The model is then tested to see whether it reads images, and the vision setting is
turned on or off from the result.

Each model becomes its own conversation agent, so a fast model for voice and a
larger one for harder questions is one provider with two models.

## 3. Use it

Assign an agent to a voice assistant under _Settings → Voice assistants_.

Nothing is remembered, and no history is readable, until you choose those
capabilities — see [what the agent can do](capabilities.md).
