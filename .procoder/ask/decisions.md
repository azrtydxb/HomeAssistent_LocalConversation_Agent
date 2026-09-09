# Decisions

## Target scope: local-first or genuinely universal?

**Decided: local-first, but large-context.** Target backends are serious OpenAI-compatible
serving stacks — fastllm proxy, LiteLLM, vLLM, SGLang. Cloud providers are explicitly out of
scope as direct targets; they are reached through LiteLLM/fastllm. Local-first does NOT imply
small context: 256K is common and 1M exists. Context budget is a latency (prefill/TTFT)
concern, not a capacity one.

## Custom function layer

**Decided: none for v1.** Rely entirely on HA's built-in Assist LLM API plus MCP servers.
Revisit only if a concrete gap appears in use.

## Voice latency / streaming

**Decided: first-class.** Stream deltas promptly, never buffer a whole response, and let the
HA pipeline start TTS as early as possible.

## Reasoning-model handling

**Decided: in scope for v1.** Superseded in detail by the findings below: reasoning is not
stripped but routed to HA's `thinking_content` field. Endpoints returning a `reasoning_content`
field map directly; models emitting inline `<think>` blocks need a hold-buffer splitter because
the tag can straddle deltas.

## Integration domain

**Decided: `local_llm_conversation`** (lowercase snake_case — hassfest rejects uppercase).
Display name: "Local LLM Conversation". Minimum HA version: TBD — pin to the release that
introduced `thinking_content`, not blindly to 2026.9.

# Findings (HA 2026.9, read from source)

- `ChatLog` exposes `thinking_content` on `AssistantContent` and
  `AssistantContentDeltaDict`. Reasoning is routed there, not stripped.
- `assist_pipeline` already streams early TTS: `chat_log_delta_listener` feeds a
  `tts_input_stream` queue and starts speaking past `STREAM_RESPONSE_CHARS = 60`,
  or on first tool call if text has accumulated. It reads only `content`, so
  `thinking_content` is excluded automatically.
- Consequence: no sentence-boundary or TTS-handoff code needed. Requirement is
  simply to emit deltas promptly and never buffer a whole response.

# Findings (live endpoint, fastllm proxy + Qwen3-6-35B-A3B, 2026-09-09)

Verified against the real endpoint rather than assumed:

1. **Reasoning field is `reasoning`, not `reasoning_content`.** vLLM and SGLang use
   `reasoning_content`; this proxy uses `reasoning`. Handling only the documented
   name silently discarded all reasoning. Both names are now accepted via
   `REASONING_KEYS`.
2. **Reasoning consumes a large share of the token budget.** A one-line question
   produced ~850 tokens of reasoning and, at `max_tokens=120`, returned
   `finish_reason: length` with completely empty content — the user would hear
   silence. Default `max_tokens` raised 2048 -> 4096, and the case now raises an
   actionable error naming the option to increase.
3. **Tool-call delta shape matches the accumulator design.** `id`, `type` and
   `name` arrive in the first delta, `arguments` in fragments, `index` present
   throughout, terminated by `finish_reason: tool_calls`. Captured verbatim into
   `test_real_endpoint_tool_call_shape`.
4. **Endpoint serves exactly one model**, so the config flow's auto-select path
   applies and no model needs to be typed.

# Open

## How to deploy to the live HA instance

**Decided: publish as a HACS custom repository** and install from there, rather
than copying files over SSH or Samba.

## Which GitHub repository to publish to

HACS does not support private repositories, so the repo must be public. The
existing remote is an empty PRIVATE repo in the `azrtydxb` org; the authenticated
gh account is `piwi3910`. The chosen URL must also be written into
`manifest.json` (`documentation`, `issue_tracker`) and `codeowners`.

- Make the existing `azrtydxb` repo public and push there.
- New public repo under the `piwi3910` account.
- New public repo under the `azrtydxb` org, named for the domain.

## BLOCKED: azrtydxb org is disabled

The repo was made public successfully, but `git push` is refused with
"Repository 'azrtydxb/HomeAssistent_LocalConversation_Agent' is disabled.
Please ask the owner to check their account." (HTTP 403). This is an
account-level problem on the org, not a permissions or protocol issue.

- Publish under `piwi3910` instead (authenticated account, working).
- Fix the azrtydxb account, then retry the push there.

## Two-tier configuration (provider -> models)

**Decided: use Home Assistant config subentries.** The config entry becomes the
provider (base URL, API key); each model is a `conversation` subentry with its own
agent entity. This is the native mechanism and is what Ollama uses in 2026.x.
Requires a config entry migration so the existing single-tier entry is not lost.

## Basic vs advanced settings

**Decided: `data_entry_flow.section` with `collapsed: True`.** Native collapsible
section; no custom UI needed.

## Assistant name / wake word consistency

`wake_word_phrase` never reaches the conversation agent: `assist_pipeline` sets it
only for duplicate-wakeup detection and does not put it on `ConversationInput`,
which carries just text, context, conversation_id, device_id, satellite_id,
language, agent_id and extra_system_prompt. So the agent cannot know at runtime
which wake word triggered it. `WakeWord` does expose id, name and phrase, so a
name can be resolved when the form is rendered.

- Configurable "Assistant name", defaulted from the preferred pipeline's wake word
  when one can be resolved, injected into the system prompt.
- Configurable only, no wake-word derivation.
- Resolve per request from satellite_id/device_id at runtime.

## What to build next

Grounded in concrete gaps in this codebase against Home Assistant 2026.9, not a
generic wishlist.

Correctness gaps (wrong today):

- No reauth flow. A rotated or revoked key leaves the entry failing with no
  prompt; Ollama implements `async_step_reauth`.
- The agent reports available even when the endpoint is unreachable, so a dead
  provider looks healthy until someone speaks to it.
- Anyone who upgraded straight to v0.3.0 still has an orphaned entity and device;
  v0.3.1 fixed the migration but does not repair a box that already ran it.

Capabilities (new):

- Vision. `UserContent.attachments` exists and is ignored by `_convert_content`,
  so camera snapshots cannot reach the model.
- AI Task. `ai_task` is a platform in 2026.9 and fits the subentry layout, giving
  automations structured data generation rather than only conversation.
- Prompted tool-calling fallback for models with no native tool support. The
  `supports_tools` switch currently just disables acting entirely.

Operability:

- Diagnostics with the key redacted. This is a public HACS integration and bug
  reports currently arrive with nothing attached.
- Repair issues for an unreachable endpoint or a model that has disappeared from
  the provider.
