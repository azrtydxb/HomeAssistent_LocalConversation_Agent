"""Constants for the Local LLM Conversation integration."""

import logging

DOMAIN = "local_llm_conversation"
LOGGER = logging.getLogger(__package__)

# Subentry type: one conversation agent per model on a provider.
SUBENTRY_TYPE_CONVERSATION = "conversation"
SUBENTRY_TYPE_AI_TASK = "ai_task_data"

# Provider level.
CONF_BASE_URL = "base_url"
CONF_API_KEY = "api_key"

# Model level.
CONF_MODEL = "model"
CONF_ASSISTANT_NAME = "assistant_name"
CONF_ADVANCED = "advanced"
CONF_PROMPT = "prompt"
CONF_MAX_TOKENS = "max_tokens"
CONF_TEMPERATURE = "temperature"
CONF_TOP_P = "top_p"
CONF_TIMEOUT = "timeout"

# Capability override. The target backends (vLLM, SGLang, LiteLLM, fastllm) all
# support tool calling, so it defaults on and exists only as an escape hatch.
CONF_SUPPORTS_TOOLS = "supports_tools"

# Reasoning is off unless asked for: it costs seconds and a large slice of the
# token budget on every turn, which a voice assistant rarely earns back.
# Set from probing the model when it is chosen, then overridable by hand.
CONF_VISION = "vision"

CONF_THINKING = "thinking"
DEFAULT_THINKING = False

DEFAULT_CONVERSATION_NAME = "Local LLM"
DEFAULT_ASSISTANT_NAME = "Jarvis"
# Reasoning models spend a large share of the budget before answering: a one-line
# request was observed using ~850 tokens of reasoning alone.
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 1.0
DEFAULT_TIMEOUT = 120

# Backends disagree on the field name for separated reasoning: vLLM and SGLang
# use "reasoning_content", fastllm proxy uses "reasoning".
REASONING_KEYS = ("reasoning_content", "reasoning")

# Bounds the tool-call round trips per user turn so a looping model cannot hang
# the pipeline forever.
MAX_TOOL_ITERATIONS = 10

# The agent's persona. {name} is replaced with the configured assistant name, so
# the agent answers to whatever the household calls it.
DEFAULT_SOUL = """You are {name}, the digital butler of this household.

You are speaking aloud. Answer in plain text, in one or two sentences unless more
is genuinely needed. No lists, no markdown, no filler openers.

How you carry yourself:
- Serve, do not lead. Propose, never impose. Act on clear instruction.
- Calm, composed, deferential. Refined English, dry wit when it fits.
- Discreet. What happens in this house stays in this house.
- Say what you did, once, and stop. Never claim something is done unless it is.
- If asked for something you cannot do, say so plainly rather than improvising.
- If you disagree, say so once, then do as asked.
- When you are wrong, own it in a sentence and carry on. Do not keep apologising.

You are a convenience, not a companion. If someone would be better served by a
person - their family, their doctor, a friend - say so and step back. Do not
diagnose, do not speculate about anyone's state of mind, and do not encourage
anyone to keep talking to you."""
