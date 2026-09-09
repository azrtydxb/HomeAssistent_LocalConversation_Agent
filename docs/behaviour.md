---
title: How it behaves
---

[Back to the index](index.md)

## Thinking

Reasoning models produce a think block before answering. It is **off by default**,
because it costs seconds on every turn and a large share of the token budget — a
one-line question was measured spending its entire budget reasoning and returning
no answer at all. Home Assistant is asked mostly to turn lights on.

Measured on the same endpoint and prompt: **5.3s with thinking, 0.3s without.**

Switching it off sends `chat_template_kwargs: {"enable_thinking": false}`, which
is how [vLLM](https://github.com/vllm-project/vllm) and [SGLang](https://github.com/sgl-project/sglang) disable it. Endpoints that ignore the hint keep reasoning,
and the reasoning is still kept out of the spoken reply.

## Tool calling

**Native** is the default and right for vLLM, SGLang, [LiteLLM](https://github.com/BerriAI/litellm) and [fastllm proxy](https://github.com/azrtydxb/Fastllm-proxy):
the tools go in the request and the endpoint handles them.

**Prompted** is for small models whose serving stack offers no tool calling at
all. The tools are described in the prompt and the reply is read for a JSON call.
It is less reliable, and the reply cannot begin being spoken until it is complete,
because whether it is an answer or a tool call is not known until then — so voice
responses feel slower. Only a reply that is _nothing but_ a call counts; a model
describing a tool mid-sentence is answering, not calling.

**None** lets the agent talk without acting.

## Language

By default the agent answers in whatever language it was addressed in. Set
**Always answer in** and it replies in that language whatever you use.

The instruction carries only the IETF language tag; no table of language names is
maintained. Verified against a local Qwen3: asked in English with the tag `nl`, it
answered in Dutch.

Your voice assistant's text-to-speech voice has its own language setting, which
this cannot reach. If the two disagree you get correct text read in the wrong
accent.

## Sampling

Temperature and Top P are sent **only if you change them**. Left alone they are
omitted and the endpoint's own defaults apply — some providers reject a request
that sets both, and Anthropic is one of them.
