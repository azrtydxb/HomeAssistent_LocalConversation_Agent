"""Tests that the translations stay in step with the English strings."""

import json
from pathlib import Path

import pytest

COMPONENT = Path("custom_components/local_llm_conversation")
STRINGS = json.loads((COMPONENT / "strings.json").read_text())
TRANSLATIONS = sorted((COMPONENT / "translations").glob("*.json"))


def keys(node, prefix=""):
    """Return every leaf key path, so structure can be compared not content."""
    found = set()
    for key, value in node.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            found |= keys(value, path + ".")
        else:
            found.add(path)
    return found


def placeholders(node):
    """Return every {placeholder} used, keyed by where it appears."""
    import re

    found = {}
    for key, value in node.items():
        if isinstance(value, dict):
            for path, names in placeholders(value).items():
                found[f"{key}.{path}"] = names
        elif isinstance(value, str):
            found[key] = set(re.findall(r"\{(\w+)\}", value))
    return found


def test_there_are_translations_to_check() -> None:
    assert TRANSLATIONS, "no translations found"


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.stem)
def test_a_translation_covers_every_string(path: Path) -> None:
    """A missing key shows as a raw key in the UI, not as English."""
    translated = json.loads(path.read_text())
    missing = keys(STRINGS) - keys(translated)
    extra = keys(translated) - keys(STRINGS)

    assert not missing, f"{path.name} is missing: {sorted(missing)[:5]}"
    assert not extra, (
        f"{path.name} has strings that no longer exist: {sorted(extra)[:5]}"
    )


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.stem)
def test_placeholders_survive_translation(path: Path) -> None:
    """A dropped {placeholder} shows the user a blank where a value should be.

    A renamed one is worse: Home Assistant raises rather than rendering the form.
    """
    translated = json.loads(path.read_text())
    english = placeholders(STRINGS)
    for location, names in placeholders(translated).items():
        assert names == english.get(location, set()), (
            f"{path.name} changed the placeholders in {location}"
        )


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.stem)
def test_a_translation_is_not_just_english(path: Path) -> None:
    """Guards a file created by copying en.json and never translated."""
    if path.stem == "en":
        return
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    translated = json.loads(path.read_text())

    def values(node):
        for value in node.values():
            if isinstance(value, dict):
                yield from values(value)
            else:
                yield value

    same = set(values(english)) & set(values(translated))
    # Some strings are legitimately identical: Top P, Temperature, AI Task.
    assert len(same) < 10, f"{path.name} looks untranslated ({len(same)} identical)"
