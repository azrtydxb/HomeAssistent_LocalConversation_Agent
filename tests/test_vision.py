"""Tests for passing camera snapshots to the model."""

import base64
from pathlib import Path

from homeassistant.components import conversation
from homeassistant.core import HomeAssistant

from custom_components.local_llm_conversation.conversation import (
    _async_load_images,
    _convert_content,
)

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def user_with(
    tmp_path: Path, *files: tuple[str, str, bytes]
) -> conversation.UserContent:
    attachments = []
    for name, mime_type, data in files:
        path = tmp_path / name
        path.write_bytes(data)
        attachments.append(
            conversation.Attachment(
                media_content_id=f"media://{name}", mime_type=mime_type, path=path
            )
        )
    return conversation.UserContent(
        content="Who is at the door?", attachments=attachments
    )


class FakeLog:
    def __init__(self, *content):
        self.content = list(content)


async def test_an_image_is_sent_alongside_the_question(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    content = user_with(tmp_path, ("door.png", "image/png", PNG))
    images = await _async_load_images(hass, FakeLog(content))
    (message,) = _convert_content(content, images)

    assert message["role"] == "user"
    text, image = message["content"]
    assert text == {"type": "text", "text": "Who is at the door?"}
    assert image["type"] == "image_url"
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    assert base64.b64decode(image["image_url"]["url"].split(",", 1)[1]) == PNG


async def test_a_message_without_attachments_stays_plain_text(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """The multi-part form costs tokens and some servers handle it worse."""
    content = conversation.UserContent(content="Turn on the light")
    (message,) = _convert_content(
        content, await _async_load_images(hass, FakeLog(content))
    )
    assert message == {"role": "user", "content": "Turn on the light"}


async def test_non_image_attachments_are_skipped(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Sending a PDF as an image_url would just make the endpoint error."""
    content = user_with(tmp_path, ("notes.pdf", "application/pdf", b"%PDF-1.4"))
    images = await _async_load_images(hass, FakeLog(content))

    assert images == {}
    (message,) = _convert_content(content, images)
    assert message["content"] == "Who is at the door?"


async def test_a_deleted_snapshot_does_not_fail_the_turn(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Camera snapshots are temporary files and may be gone by the next turn."""
    content = user_with(tmp_path, ("gone.png", "image/png", PNG))
    content.attachments[0].path.unlink()

    images = await _async_load_images(hass, FakeLog(content))
    assert images == {}
    (message,) = _convert_content(content, images)
    assert message["content"] == "Who is at the door?"


async def test_several_images_all_reach_the_model(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    content = user_with(
        tmp_path,
        ("front.png", "image/png", PNG),
        ("back.jpg", "image/jpeg", PNG),
    )
    images = await _async_load_images(hass, FakeLog(content))
    (message,) = _convert_content(content, images)

    kinds = [part["type"] for part in message["content"]]
    assert kinds == ["text", "image_url", "image_url"]
    assert message["content"][2]["image_url"]["url"].startswith("data:image/jpeg;")
