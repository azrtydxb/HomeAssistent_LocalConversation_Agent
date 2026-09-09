"""Minimal client for OpenAI-compatible chat completion endpoints.

Deliberately not the ``openai`` SDK: the targets here are vLLM, SGLang, LiteLLM
and fastllm proxy, whose responses are close to but not exactly OpenAI's. The
SDK's schema validation rejects harmless divergences, and it is a heavy
dependency for what amounts to one POST and an SSE reader.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import aiohttp
from homeassistant.exceptions import HomeAssistantError

from .const import LOGGER


class CannotConnect(HomeAssistantError):
    """The endpoint could not be reached at all."""


class InvalidAuth(HomeAssistantError):
    """The endpoint rejected the API key."""


_DONE = "[DONE]"
# Enough of an error body to identify the problem without flooding the log.
_MAX_ERROR_BODY = 500


def normalize_base_url(raw: str) -> str:
    """Return the API root for a user-supplied URL.

    Users paste anything from ``http://host:8000`` to a full completions URL.
    Everything is reduced to the root the endpoints hang off.
    """
    url = raw.strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    if not url.endswith("/v1"):
        url = f"{url}/v1"
    return url


class ChatCompletionsClient:
    """Talks to a single OpenAI-compatible endpoint."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        api_key: str | None,
        timeout: int,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._base_url = normalize_base_url(base_url)
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=None, sock_read=timeout)

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def async_list_models(self) -> list[str]:
        """Return the model ids the endpoint advertises.

        Raises CannotConnect if the endpoint is unreachable and InvalidAuth if it
        rejects the key. An empty list means the endpoint answered but does not
        expose a model listing, which is not an error: the model is then typed in
        by hand.
        """
        try:
            async with self._session.get(
                f"{self._base_url}/models",
                headers=self._headers,
                timeout=self._timeout,
            ) as response:
                if response.status in (401, 403):
                    raise InvalidAuth(
                        f"Endpoint rejected the API key ({response.status})"
                    )
                if response.status != 200:
                    LOGGER.debug(
                        "%s/models returned %s; the model must be entered by hand",
                        self._base_url,
                        response.status,
                    )
                    return []
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f"Could not reach {self._base_url}: {err}") from err
        except ValueError as err:
            # Reachable, but not speaking the OpenAI API.
            LOGGER.debug("%s/models returned unparseable JSON: %s", self._base_url, err)
            return []

        if not isinstance(payload, dict):
            return []
        return sorted(
            model["id"]
            for model in payload.get("data", [])
            if isinstance(model, dict) and model.get("id")
        )

    async def async_stream_chat(
        self, payload: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any]]:
        """POST a chat completion and yield each streamed choice delta."""
        url = f"{self._base_url}/chat/completions"
        LOGGER.debug("Requesting %s with model %s", url, payload.get("model"))

        try:
            async with self._session.post(
                url,
                headers=self._headers,
                json={**payload, "stream": True},
                timeout=self._timeout,
            ) as response:
                if response.status != 200:
                    body = (await response.text())[:_MAX_ERROR_BODY]
                    raise HomeAssistantError(
                        f"LLM endpoint returned {response.status}: {body}"
                    )

                async for line in response.content:
                    delta = _parse_sse_line(line)
                    if delta is not None:
                        yield delta
        except TimeoutError as err:
            raise HomeAssistantError(
                "Timed out waiting for the LLM endpoint to respond"
            ) from err
        except aiohttp.ClientError as err:
            raise HomeAssistantError(
                f"Could not reach the LLM endpoint: {err}"
            ) from err


def _parse_sse_line(raw: bytes) -> dict[str, Any] | None:
    """Return the choice delta in an SSE line, or None if there is nothing to yield."""
    line = raw.decode("utf-8", errors="replace").strip()
    # Blank lines separate events; lines starting with ':' are keep-alive comments.
    if not line or line.startswith(":") or not line.startswith("data:"):
        return None

    data = line[len("data:") :].strip()
    if data == _DONE:
        return None

    try:
        chunk = json.loads(data)
    except json.JSONDecodeError:
        LOGGER.debug("Ignoring unparseable SSE payload: %s", data[:200])
        return None

    choices = chunk.get("choices")
    if not choices:
        # Usage-only chunks carry no choices and are not an error.
        return None
    return choices[0]
