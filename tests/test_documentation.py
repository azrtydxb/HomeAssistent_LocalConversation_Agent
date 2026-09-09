"""Tests that the documentation keeps up with the integration.

The README drifted badly before these existed: it described options by names they
no longer had, and a table had two rows orphaned below the section that followed
it. Nothing failed, because nothing was looking.
"""

import json
import re
from pathlib import Path

import pytest

COMPONENT = Path("custom_components/local_llm_conversation")
README = Path("README.md")
DOCS = sorted(Path("docs").glob("*.md"))
PAGES = [README, *DOCS]
ALL_TEXT = "\n".join(path.read_text() for path in PAGES)
STRINGS = json.loads((COMPONENT / "strings.json").read_text())

FASTLLM_REPO = "https://github.com/azrtydxb/Fastllm-proxy"


def option_labels(node: dict) -> set[str]:
    """Return every label a person sees on a configuration form."""
    labels: set[str] = set()
    for key, value in node.items():
        if key == "data" and isinstance(value, dict):
            labels |= {str(v) for v in value.values()}
        elif isinstance(value, dict):
            labels |= option_labels(value)
    return labels


# Section headings a person navigates by, not settings.
NOT_AN_OPTION = {"Advanced"}


def test_there_are_documentation_pages() -> None:
    assert DOCS, "no pages found under docs/"


@pytest.mark.parametrize("label", sorted(option_labels(STRINGS) - NOT_AN_OPTION))
def test_every_option_is_documented(label: str) -> None:
    """An option nobody documented is one nobody can find."""
    assert label in ALL_TEXT, f"nothing documents the {label!r} option"


def test_the_docs_do_not_describe_options_that_no_longer_exist() -> None:
    """Names that were renamed and left behind in the documentation."""
    for name in ("Instructions", "Endpoint supports tool calling"):
        assert f"**{name}**" not in ALL_TEXT, f"documentation still lists {name!r}"


@pytest.mark.parametrize("path", PAGES, ids=lambda p: str(p))
def test_every_table_row_sits_in_a_table(path: Path) -> None:
    """A row separated from its header renders as stray pipes.

    This happened: two rows ended up below the section that followed the table.
    """
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        above = lines[index - 1].strip() if index else ""
        below = lines[index + 1].strip() if index + 1 < len(lines) else ""
        is_continuation = above.startswith("|")
        is_header = bool(below) and set(below.replace("|", "").replace(" ", "")) <= {
            "-",
            ":",
        }
        assert is_continuation or is_header, (
            f"{path} line {index + 1} is a table row with no table: {line[:60]}"
        )


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.stem)
def test_links_between_pages_point_at_pages_that_exist(path: Path) -> None:
    """A dead link between pages is only noticed by a reader who hits it."""
    for target in re.findall(
        r"\]\((?!https?:)([^)#]+\.md)(?:#[^)]*)?\)", path.read_text()
    ):
        assert (path.parent / target).exists(), f"{path} links to missing {target}"


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.stem)
def test_every_page_has_front_matter(path: Path) -> None:
    """Without it Jekyll serves the raw markdown instead of a themed page."""
    text = path.read_text()
    assert text.startswith("---\n"), f"{path} has no front matter"
    assert "title:" in text.split("---")[1], f"{path} has no title"


def test_fastllm_is_linked_where_it_is_mentioned() -> None:
    """It is ours; a reader who sees the name should be able to reach it."""
    for path in PAGES:
        text = path.read_text()
        if "fastllm" in text:
            assert FASTLLM_REPO in text, f"{path} names fastllm without linking it"


def test_the_readme_stays_short() -> None:
    """It is the HACS listing, not the manual.

    Everything long belongs on the documentation site; a README that grows back
    into a manual is how the last one rotted.
    """
    assert len(README.read_text().splitlines()) < 90


def test_the_readme_points_at_the_documentation_site() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    site = manifest["documentation"]
    assert site.startswith("https://"), "documentation should be a URL"
    assert site in README.read_text(), "the README does not link the documentation"


def test_the_help_link_in_home_assistant_points_at_the_docs() -> None:
    """manifest documentation is what the ? in the integration UI opens."""
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert "github.io" in manifest["documentation"], (
        "the ? in Home Assistant should open the documentation site"
    )


def test_the_minimum_home_assistant_version_matches_hacs() -> None:
    """Two places state it; they disagree silently when one is bumped."""
    version = json.loads(Path("hacs.json").read_text())["homeassistant"]
    assert version in ALL_TEXT, f"documentation does not state the minimum {version}"
