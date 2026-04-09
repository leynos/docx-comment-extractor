"""Unit tests for the document extractor."""

from __future__ import annotations

import typing as typ

from docx_comment_extractor.extractor import extract_document
from tests.support_documents import build_fixture

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_extract_document_builds_simple_model(tmp_path: Path) -> None:
    """A single commented run should be represented in the block model."""
    document_path = build_fixture("simple-comment", tmp_path / "simple.docx")

    result = extract_document(document_path)

    assert result.warnings == ()
    assert [block.kind for block in result.document.blocks] == ["heading", "paragraph"]
    assert result.document.comments[0].author == "Sam C"
    assert result.document.comments[0].body == "Needs evidence."
    paragraph_fragments = result.document.blocks[1].fragments
    assert paragraph_fragments[1].start_comment_ids == ("0",)
    assert paragraph_fragments[1].end_comment_ids == ("0",)


def test_extract_document_supports_multi_run_ranges(tmp_path: Path) -> None:
    """A comment spanning multiple runs should start and end on separate fragments."""
    document_path = build_fixture("multi-run-comment", tmp_path / "multi-run.docx")

    result = extract_document(document_path)

    paragraph_fragments = result.document.blocks[0].fragments
    assert paragraph_fragments[1].start_comment_ids == ("0",)
    assert paragraph_fragments[3].end_comment_ids == ("0",)


def test_extract_document_supports_cross_paragraph_ranges(tmp_path: Path) -> None:
    """A comment spanning paragraphs should remain open until the second block."""
    document_path = build_fixture("cross-paragraph-comment", tmp_path / "cross.docx")

    result = extract_document(document_path)

    first_block = result.document.blocks[0]
    second_block = result.document.blocks[1]
    assert first_block.fragments[1].start_comment_ids == ("0",)
    assert second_block.fragments[0].end_comment_ids == ("0",)
    assert result.document.comments[0].body == (
        "This crosses a paragraph boundary. / Second note paragraph."
    )


def test_extract_document_warns_for_tables(tmp_path: Path) -> None:
    """Unsupported top-level tables should emit a warning rather than failing."""
    document_path = build_fixture("table-document", tmp_path / "table.docx")

    result = extract_document(document_path)

    assert len(result.warnings) == 1
    assert result.warnings[0].code == "unsupported-block"
