"""Constants for the Local LLM Conversation integration."""

import logging

DOMAIN = "local_llm_conversation"
LOGGER = logging.getLogger(__package__)

CONF_BASE_URL = "base_url"
CONF_API_KEY = "api_key"
CONF_MODEL = "model"
CONF_PROMPT = "prompt"
CONF_MAX_TOKENS = "max_tokens"
CONF_TEMPERATURE = "temperature"
CONF_TOP_P = "top_p"
CONF_TIMEOUT = "timeout"

# Capability overrides. The target backends (vLLM, SGLang, LiteLLM, fastllm)
# all support these, so they default on and exist only as escape hatches.
CONF_SUPPORTS_TOOLS = "supports_tools"
CONF_SUPPORTS_STREAMING = "supports_streaming"

# Backends disagree on the field name for separated reasoning: vLLM and SGLang
# use "reasoning_content", fastllm proxy uses "reasoning".
REASONING_KEYS = ("reasoning_content", "reasoning")

# Reasoning models spend a large share of the budget before answering: a
# one-line request was observed using ~850 tokens of reasoning alone.
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 1.0
DEFAULT_TIMEOUT = 120

# Bounds the tool-call round trips per user turn so a looping model cannot
# hang the pipeline forever.
MAX_TOOL_ITERATIONS = 10
