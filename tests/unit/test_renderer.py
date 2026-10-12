"""Unit tests for Markdown rendering."""

from __future__ import annotations

import datetime as dt
import typing as typ

import pytest

from docx_comment_extractor.extractor import extract_document
from docx_comment_extractor.models import Block, Comment, DocumentModel, Fragment
from docx_comment_extractor.renderer import (
    CRITICMARKUP_ESCAPE_SEQUENCES,
    escape_criticmarkup_text,
    format_comment_reference,
    render_document,
)
from tests.support_documents import build_fixture

if typ.TYPE_CHECKING:
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion


def test_format_comment_reference_with_metadata() -> None:
    """Author and timestamp should be included when present."""
    comment = Comment(
        comment_id="7",
        author="Reviewer",
        body="Tighten this sentence.",
        timestamp=dt.datetime(2026, 4, 9, 20, 35, 31, tzinfo=dt.UTC),
    )

    assert (
        format_comment_reference(comment)
        == "Reviewer, 2026-04-09T20:35:31Z: Tighten this sentence."
    ), "comment references should render author, UTC timestamp, and body"


@pytest.mark.parametrize(
    ("author", "body", "timestamp", "expected"),
    [
        (
            "Reviewer",
            "",
            dt.datetime(2026, 4, 9, 20, 35, 31, tzinfo=dt.UTC),
            "Reviewer, 2026-04-09T20:35:31Z",
        ),
        ("Reviewer", "", None, "Reviewer"),
        (
            None,
            "",
            dt.datetime(2026, 4, 9, 20, 35, 31, tzinfo=dt.UTC),
            "2026-04-09T20:35:31Z",
        ),
        (None, "Tighten this sentence.", None, "Tighten this sentence."),
        (None, "", None, ""),
    ],
)
def test_format_comment_reference_edge_cases(
    author: str | None,
    body: str,
    timestamp: dt.datetime | None,
    expected: str,
) -> None:
    """Comment references should omit separators around absent fields."""
    comment = Comment(
        comment_id="test",
        author=author,
        body=body,
        timestamp=timestamp,
    )

    assert format_comment_reference(comment) == expected, (
        "comment references should omit separators around missing fields"
    )


def test_escape_criticmarkup_text() -> None:
    """Literal CriticMarkup delimiters should be neutralised."""
    assert (
        escape_criticmarkup_text("{==alpha==} {>>beta<<}")
        == "\\{==alpha==\\} \\{>>beta<<\\}"
    ), "literal CriticMarkup highlight and comment markers should be escaped"


@pytest.mark.parametrize(
    ("source", "replacement"),
    CRITICMARKUP_ESCAPE_SEQUENCES,
)
def test_escape_criticmarkup_text_handles_every_delimiter(
    source: str,
    replacement: str,
) -> None:
    """Every supported CriticMarkup delimiter should be neutralized."""
    assert escape_criticmarkup_text(source) == replacement, (
        "each configured CriticMarkup delimiter should use its mapped escape"
    )


def test_render_document_neutralizes_html_and_unsafe_links() -> None:
    """Untrusted document and comment text should remain literal Markdown text."""
    unsafe_text = '<script>alert("x")</script> [run](javascript:alert(1))'
    document = DocumentModel(
        blocks=(
            Block(
                kind="paragraph",
                fragments=(
                    Fragment(
                        text=unsafe_text,
                        start_comment_ids=("0",),
                        end_comment_ids=("0",),
                    ),
                ),
            ),
        ),
        comments=(
            Comment(
                comment_id="0",
                author="<script>author</script>",
                body=unsafe_text,
            ),
        ),
    )

    rendered = render_document(document)

    assert "<script>" not in rendered, "raw HTML tags should be entity-escaped"
    assert "[run](javascript:" not in rendered, (
        "unsafe Markdown links should not remain active link syntax"
    )
    assert "&lt;script&gt;" in rendered, (
        "escaped raw HTML should remain legible as literal text"
    )


def test_render_extracted_carriage_return_opener_is_inert(
    tmp_path: Path,
) -> None:
    """A carriage return from a real DOCX should not leave an opener live.

    This is the end-to-end form of the CR calibration: the CR arrives as a
    numeric character reference in ``w:t``, which is the only serialized shape
    that survives XML parsing as a carriage return. The guard that anchored with
    ``(?m:^)`` treated the opener after it as inert, and CommonMark then opened
    an HTML block that ran to the next blank line -- over the renderer's own
    CriticMarkup markers.
    """
    from markdown_it import MarkdownIt

    document_path = build_fixture("carriage-return", tmp_path / "cr.docx")

    result = extract_document(document_path)
    extracted = "".join(
        fragment.text
        for block in result.document.blocks
        for fragment in block.fragments
    )
    assert "safe\r<!--" in extracted, (
        f"the fixture should deliver a real carriage return, not {extracted!r}"
    )

    rendered = render_document(result.document)
    markdown = MarkdownIt("commonmark")
    stack = list(markdown.parse(rendered))
    blocks = []
    while stack:
        token = stack.pop()
        if token.type in {"html_block", "html_inline"}:
            blocks.append(token.content)
        stack.extend(token.children or [])
    assert not blocks, f"a carriage-return opener stayed live in {rendered!r}"


def test_render_document_snapshot_for_cross_paragraph_comment(
    tmp_path: Path,
    snapshot: SnapshotAssertion,
) -> None:
    """Rendered Markdown should stay stable for cross-paragraph spans."""
    document_path = build_fixture("cross-paragraph-comment", tmp_path / "cross.docx")

    result = extract_document(document_path)

    assert render_document(result.document) == snapshot, (
        "cross-paragraph CriticMarkup rendering should match the approved snapshot"
    )


def test_render_document_snapshot_for_criticmarkup_literals(
    tmp_path: Path,
    snapshot: SnapshotAssertion,
) -> None:
    """Literal CriticMarkup content should stay escaped in rendered output."""
    document_path = build_fixture("criticmarkup-literal", tmp_path / "literal.docx")

    result = extract_document(document_path)

    assert render_document(result.document) == snapshot, (
        "literal CriticMarkup rendering should match the approved escaped snapshot"
    )
