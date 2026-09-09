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
