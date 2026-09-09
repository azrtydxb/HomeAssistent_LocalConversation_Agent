---
title: Configuration reference
---

[Back to the index](index.md)

## Provider

| Option       | Notes                                                |
| ------------ | ---------------------------------------------------- |
| **Base URL** | For example `http://192.168.1.10:8000`.              |
| **API key**  | Leave empty if the endpoint needs no authentication. |

## Model

| Option                     | Notes                                                                   |
| -------------------------- | ----------------------------------------------------------------------- |
| **Name**                   | What the agent is called in Home Assistant, for example Voice or Study. |
| **Model**                  | Chosen from the provider's catalogue, or typed by hand.                 |
| **Control Home Assistant** | Which tool APIs the model may use. Without one it can talk but not act. |
| **Assistant name**         | What the agent calls itself. Match your wake word. Defaults to Jarvis.  |

## Advanced

Folded away, because most people never touch it.

| Option                             | Notes                                                                     |
| ---------------------------------- | ------------------------------------------------------------------------- |
| **Soul**                           | The agent's persona. `{name}` is replaced with the assistant name.        |
| **What it knows about this house** | Knowledge files to enable. Only appears once you have written one.        |
| **Send images to this model**      | Set by testing the model when you pick it. Override if you prefer.        |
| **Let the model think first**      | Off by default. See [thinking](behaviour.md#thinking).                    |
| **Always answer in**               | Empty means answer in the language you were addressed in.                 |
| **How the model calls tools**      | Native, prompted, or none. See [tool calling](behaviour.md#tool-calling). |
| **Maximum tokens to return**       | How long a reply may be. Do not set it low on a reasoning model.          |
| **Temperature** / **Top P**        | Sent only if you change them. See [sampling](behaviour.md#sampling).      |
| **Response timeout (seconds)**     | How long to wait between streamed tokens before giving up.                |

## The soul

Every agent ships with a persona rather than a bare system prompt, so it behaves
like the same assistant every time. The default is a discreet household butler:
brief spoken answers, no lists or markdown, no filler.

`{name}` is replaced with the assistant name, so renaming the agent renames the
character. Replace the text entirely if you want something else.

## What it knows about this house

The soul says who the agent is. Knowledge files say what it knows about your
particular house: how the heating is zoned, what an oddly named sensor means, the
rules for the holiday cottage.

Put markdown files in `config/local_llm_conversation/knowledge/` and enable them
per agent. The picker only appears once you have written something.

Every enabled file is added to the prompt on **every** request, including "turn on
the kitchen light", so enable only what earns it. Files over 32 KB are skipped for
the same reason.

Deliberately just files and a picker — no download service, no registry. If
editing the soul covers what you need, use the soul.
