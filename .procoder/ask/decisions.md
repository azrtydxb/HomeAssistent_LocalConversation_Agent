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
"Repository 'azrtydxb/ha-local-llm-conversation' is disabled.
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

## Analysis of extended_openai_conversation (2026-09-09)

Repo state: 1436 stars, 127 open issues, actively maintained (v3.0.0, pushed
2026-09-06). It has modernised too - it now has `ai_task.py` and `entity.py` - so
this is not a comparison against an abandoned project.

Its top open issue by reactions is #220 "Migrate to native LLM API support in
Home Assistant" (30 reactions, far ahead of anything else). That is the design we
already have.

Deliberately not adopting - its function DSL:
`sqlite`, `scrape`, `bash`, `file`, `web`, `composite`, and the `execute_service`
native function. `bash` and `file` in particular give a model shell and filesystem
access on the Home Assistant host. `execute_service` lets the model call any
service regardless of what is exposed. Home Assistant's own LLM API covers the
legitimate uses.

Already covered: native LLM API (#220), streaming (#58), AI Task, vision,
attaching the user's name (Home Assistant does this itself in
`async_provide_llm_data`).

Real gaps found:

1. Both `temperature` and `top_p` are always sent (their #428). Anthropic rejects
   a request carrying both. Latent here rather than breaking, since the proxy in
   use fronts other providers.
2. No history, energy or statistics access. Their `get_history`, `get_energy` and
   `get_statistics` native functions have no equivalent in Home Assistant's Assist
   API, which is intent-based plus GetLiveContext. Their #157 shows the trap:
   an unbounded history query overflows the context window.
3. No memory across conversations (their #241).
4. No automation creation (their `add_automation`).
5. No way to see the prompt that was actually sent (their #283).
6. English only; they ship 10+ translations.
7. Skills - reusable capability modules loaded from disk. Overlaps what the soul
   already does for persona.

## Working through the filed issues

No decision needed, starting on these:

- #1 sampling parameters: send only what differs from the default.
- #5 prompt inspection: a debug log line behind the existing logger is the
  smallest thing that works and keeps house data out of files people paste.
- #6 translations: mechanical, but quality cannot be verified for languages I
  cannot read, so machine output would be shipped unchecked.

Decisions needed:

### #8 language override: form shape

Home Assistant config flow forms have no conditional visibility, so a checkbox
plus a dropdown puts both on screen with the dropdown inert while unchecked.

- Checkbox plus dropdown, as asked, accepting the inert field.
- One language selector where "Automatic" means match the input.

### #2 history, energy and statistics: scope

- All three, bounded: GetHistory, GetStatistics, GetEnergy.
- History only, as the common case.
- Statistics only, since long-run questions are what people ask.

### #3 memory: approach

- Model-controlled via a Remember tool, stored in .storage, pruned by count.
- Person-controlled: a text field in options the household edits by hand.
- Not built; close the issue.

### #4 automations: safety model

- Propose only: draft it, a person confirms before it exists.
- Create disabled, never edit or delete existing ones.
- Not built; close the issue.

### #7 skills

Recommend deferring: the soul covers the persona half, and every enabled skill is
paid on every utterance in prompt size.

## Publishing to the default HACS registry

Every inclusion requirement is met: public, described, issues enabled, five
topics, not archived, 25 releases, a brand icon, `hacs.json` with a name, the HACS
and hassfest actions both green with no `ignore` key, and the submitter credited
with every commit and an org member.

Submission is a pull request to `hacs/default` adding one line to `integration`,
which sorts between `azogue/eventsensor` and `azsaurr/ha_usms`.

Open first: the repository is named `ha-local-llm-conversation`, with
"Assistent" misspelled. HACS lists by full repository name, so that spelling
becomes the public listing and the entry in `hacs/default`. Renaming afterwards
means a second pull request there, and changes the documentation site URL, which
is also the link behind the ? in Home Assistant.
- Rename the repository first, then submit.
- Submit as it is; the name is not worth the churn.
