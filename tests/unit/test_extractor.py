"""Unit tests for the document extractor."""

from __future__ import annotations

import datetime as dt
import typing as typ

import pytest
from docx import Document

from docx_comment_extractor.extractor import ExtractionError, extract_document
from tests.support_documents import build_fixture

if typ.TYPE_CHECKING:
    from pathlib import Path

    from docx.document import Document as WordDocument


def test_extract_document_uses_injected_document_loader(tmp_path: Path) -> None:
    """The public query should delegate package I/O to its loader boundary."""
    document_path = build_fixture("simple-comment", tmp_path / "simple.docx")
    loaded_paths: list[Path] = []

    def load_document(path: Path) -> WordDocument:
        """Record and load the requested package."""
        loaded_paths.append(path)
        return Document(str(path))

    result = extract_document(document_path, document_loader=load_document)

    assert loaded_paths == [document_path], (
        "the injected loader should receive the path"
    )
    assert result.document.comments, "extraction should use the injected document"


def test_extract_document_wraps_loader_failures(tmp_path: Path) -> None:
    """Loader failures should cross the public API as an extraction error."""
    document_path = tmp_path / "broken.docx"

    def fail_to_load(path: Path) -> typ.NoReturn:
        """Simulate an unreadable document package."""
        del path
        message = "storage detail"
        raise OSError(message)

    with pytest.raises(ExtractionError, match="Could not extract the Word document"):
        extract_document(document_path, document_loader=fail_to_load)


def test_extract_document_builds_simple_model(tmp_path: Path) -> None:
    """A single commented run should be represented in the block model."""
    document_path = build_fixture("simple-comment", tmp_path / "simple.docx")

    result = extract_document(document_path)

    assert result.warnings == (), "the simple model should not contain warnings"
    assert [block.kind for block in result.document.blocks] == [
        "heading",
        "paragraph",
    ], "the simple model should preserve heading and paragraph block kinds"
    assert result.document.comments[0].author == "Sam C", (
        "the extracted comment should preserve its normalized author"
    )
    assert result.document.comments[0].body == "Needs evidence.", (
        "the extracted comment should preserve its body"
    )
    paragraph_fragments = result.document.blocks[1].fragments
    assert paragraph_fragments[1].start_comment_ids == ("0",), (
        "the commented fragment should open comment boundary 0"
    )
    assert paragraph_fragments[1].end_comment_ids == ("0",), (
        "the commented fragment should close comment boundary 0"
    )


def test_extract_document_normalizes_comment_metadata(tmp_path: Path) -> None:
    """Blank authors and naive timestamps should normalize predictably."""
    document_path = build_fixture(
        "comment-normalization",
        tmp_path / "comment-normalization.docx",
    )

    result = extract_document(document_path)

    assert result.warnings == (), "metadata normalization should not emit warnings"
    comment = result.document.comments[0]
    assert comment.author is None, "a whitespace-only author should normalize to None"
    assert comment.timestamp == dt.datetime(
        2026,
        4,
        9,
        20,
        35,
        31,
        tzinfo=dt.UTC,
    ), "a naive comment timestamp should normalize to UTC"


def test_extract_document_supports_multi_run_ranges(tmp_path: Path) -> None:
    """A comment spanning multiple runs should start and end on separate fragments."""
    document_path = build_fixture("multi-run-comment", tmp_path / "multi-run.docx")

    result = extract_document(document_path)

    paragraph_fragments = result.document.blocks[0].fragments
    assert paragraph_fragments[1].start_comment_ids == ("0",), (
        "the first commented run should open the multi-run boundary"
    )
    assert paragraph_fragments[3].end_comment_ids == ("0",), (
        "the last commented run should close the multi-run boundary"
    )


def test_extract_document_supports_cross_paragraph_ranges(tmp_path: Path) -> None:
    """A comment spanning paragraphs should remain open until the second block."""
    document_path = build_fixture("cross-paragraph-comment", tmp_path / "cross.docx")

    result = extract_document(document_path)

    first_block = result.document.blocks[0]
    second_block = result.document.blocks[1]
    assert first_block.fragments[1].start_comment_ids == ("0",), (
        "the first block should open the cross-paragraph boundary"
    )
    assert second_block.fragments[0].end_comment_ids == ("0",), (
        "the second block should close the cross-paragraph boundary"
    )
    assert result.document.comments[0].body == (
        "This crosses a paragraph boundary. / Second note paragraph."
    ), "the cross-paragraph comment body should retain both normalized paragraphs"


def test_extract_document_warns_for_tables(tmp_path: Path) -> None:
    """Unsupported top-level tables should emit a warning rather than failing."""
    document_path = build_fixture("table-document", tmp_path / "table.docx")

    result = extract_document(document_path)

    assert len(result.warnings) == 1, "one table should produce one extraction warning"
    assert result.warnings[0].code == "unsupported-block", (
        "the table warning should use the unsupported-block code"
    )
    assert [
        "".join(fragment.text for fragment in block.fragments)
        for block in result.document.blocks
    ] == [
        "Before table.",
        "After table.",
    ], "table skipping should preserve the surrounding paragraph order"
