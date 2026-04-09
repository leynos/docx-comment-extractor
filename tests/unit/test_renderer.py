"""Unit tests for Markdown rendering."""

from __future__ import annotations

import datetime as dt
import typing as typ
from pathlib import Path

from docx_comment_extractor.extractor import extract_document
from docx_comment_extractor.models import Comment
from docx_comment_extractor.renderer import (
    escape_criticmarkup_text,
    format_comment_reference,
    heading_level_for_style,
    render_document,
)
from tests.support_documents import build_fixture

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def test_heading_level_for_style() -> None:
    """Heading styles should map to Markdown ATX levels."""
    assert heading_level_for_style("Heading 1") == 1
    assert heading_level_for_style("Heading2") == 2
    assert heading_level_for_style("Body Text") is None


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
    )


def test_escape_criticmarkup_text() -> None:
    """Literal CriticMarkup delimiters should be neutralised."""
    assert (
        escape_criticmarkup_text("{==alpha==} {>>beta<<}")
        == "\\{==alpha==\\} \\{>>beta<<\\}"
    )


def test_render_document_snapshot_for_cross_paragraph_comment(
    tmp_path: Path,
    snapshot: SnapshotAssertion,
) -> None:
    """Rendered Markdown should stay stable for cross-paragraph spans."""
    document_path = build_fixture("cross-paragraph-comment", tmp_path / "cross.docx")

    result = extract_document(document_path)

    assert render_document(result.document) == snapshot


def test_render_document_snapshot_for_criticmarkup_literals(
    tmp_path: Path,
    snapshot: SnapshotAssertion,
) -> None:
    """Literal CriticMarkup content should stay escaped in rendered output."""
    document_path = build_fixture("criticmarkup-literal", tmp_path / "literal.docx")

    result = extract_document(document_path)

    assert render_document(result.document) == snapshot


def test_render_sample_document_excerpt_snapshot(snapshot: SnapshotAssertion) -> None:
    """The provided sample document should keep a stable opening excerpt."""
    result = extract_document(Path("commented-pentagon-draft-sam-c.docx"))
    rendered = render_document(result.document)

    assert rendered.count("{>>") == 247
    assert rendered.count("{==") == 247
    excerpt = "\n".join(rendered.splitlines()[:40])
    assert excerpt == snapshot
