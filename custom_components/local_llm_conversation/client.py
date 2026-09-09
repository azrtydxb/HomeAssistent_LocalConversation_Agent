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

# A 256x256 solid PNG, 762 bytes. Large enough that a model which encodes it adds
# an unmistakable number of prompt tokens, small enough to send twice on a form.
_PROBE_IMAGE = (
    "iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAIAAADTED8xAAAB+0lEQVR42u3TQQ0AAAjE"
    "MED5SeeNBloJS9ZJCr4aCTAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAM"
    "AAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4AB"
    "wABgADAAGAAMgAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwA"
    "BgADgAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgADgAHA"
    "AGAAMAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAG"
    "AAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAATAA"
    "GAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYA"
    "A4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAbAAGAAMAAY"
    "AAwABgADgAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgAD"
    "gAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgADgAHgWu7LA4CJx71QAAAAAElFTkSu"
    "QmCC"
)

# Encoding an image costs far more than this; dropping it costs nothing.
_VISION_TOKEN_MARGIN = 10
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

    async def async_probe_vision(self, model: str) -> bool | None:
        """Return whether the model actually looks at images.

        A rejected image would be easy to detect, but servers do not reject it:
        vLLM answers 200 and silently drops the image for a text-only model. What
        cannot be faked is the token count - encoding an image costs prompt
        tokens, dropping it costs none. Returns None when the endpoint reports no
        usage and the question cannot be settled.
        """
        text_only = await self._async_probe_tokens(model, image=False)
        with_image = await self._async_probe_tokens(model, image=True)
        if text_only is None or with_image is None:
            return None
        return with_image - text_only >= _VISION_TOKEN_MARGIN

    async def _async_probe_tokens(self, model: str, *, image: bool) -> int | None:
        """Return prompt_tokens for a minimal request, or None if unavailable."""
        content: list[dict[str, Any]] = [{"type": "text", "text": "hi"}]
        if image:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{_PROBE_IMAGE}"},
                }
            )
        payload = {
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": content}],
        }
        try:
            async with self._session.post(
                f"{self._base_url}/chat/completions",
                headers=self._headers,
                json=payload,
                timeout=self._timeout,
            ) as response:
                if response.status != 200:
                    # 4xx means the request was understood and refused, which for
                    # an image request means the model takes no images. A 5xx is
                    # the server failing, and says nothing about the model.
                    if image and 400 <= response.status < 500:
                        return 0
                    LOGGER.debug(
                        "Vision probe for %s got %s: %s",
                        model,
                        response.status,
                        (await response.text())[:_MAX_ERROR_BODY],
                    )
                    return None
                body = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            LOGGER.debug("Vision probe failed for %s: %s", model, err)
            return None
        usage = body.get("usage") or {}
        tokens = usage.get("prompt_tokens")
        return tokens if isinstance(tokens, int) else None

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
                if response.status in (401, 403):
                    raise InvalidAuth(
                        f"Endpoint rejected the API key ({response.status})"
                    )
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
