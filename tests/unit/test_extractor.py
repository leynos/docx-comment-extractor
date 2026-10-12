"""Unit tests for the document extractor."""

from __future__ import annotations

import datetime as dt
import typing as typ
import zlib
from zipfile import BadZipFile

import pytest
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml import OxmlElement
from docx.oxml.exceptions import XmlchemyError
from docx.oxml.ns import qn
from lxml import etree
from lxml.etree import XMLSyntaxError

from docx_comment_extractor import extractor
from docx_comment_extractor.extractor import (
    ExtractionError,
    _extract_paragraph_block,
    _heading_level_for_word_style,
    extract_document,
)
from docx_comment_extractor.renderer import render_document
from tests.support_documents import build_fixture
from tests.support_renderer_oracles import (
    composition_violation,
    has_active_bracket,
    preceding_backslash_run,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from docx.document import Document as WordDocument
    from docx.text.paragraph import Paragraph
    from docx.text.run import Run


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
    document_path = build_fixture("simple-comment", tmp_path / "broken.docx")
    loaded_paths: list[Path] = []

    def fail_to_load(path: Path) -> typ.NoReturn:
        """Simulate an unreadable document package."""
        loaded_paths.append(path)
        message = "storage detail"
        raise OSError(message)

    with pytest.raises(ExtractionError, match="Could not extract the Word document"):
        extract_document(document_path, document_loader=fail_to_load)

    assert loaded_paths == [document_path], (
        "the injected loader should be reached for a valid package"
    )


def _create_xml_syntax_error() -> XMLSyntaxError:
    """Produce the parser-originated XML exception used by a corrupt package."""
    try:
        etree.fromstring(b"<")
    except XMLSyntaxError as error:
        return error
    message = "The malformed XML fixture should raise XMLSyntaxError."
    raise AssertionError(message)


@pytest.mark.parametrize(
    "failure_factory",
    [
        _create_xml_syntax_error,
        lambda: zlib.error("invalid compressed payload"),
        lambda: EOFError("unexpected end of package"),
        lambda: RuntimeError("unexpected package state"),
    ],
)
def test_extract_document_wraps_corrupt_package_failures(
    tmp_path: Path,
    failure_factory: typ.Callable[[], Exception],
) -> None:
    """Known corrupt-package errors should not cross the public API boundary."""
    document_path = build_fixture("simple-comment", tmp_path / "corrupt.docx")
    loaded_paths: list[Path] = []

    def fail_to_load(path: Path) -> typ.NoReturn:
        loaded_paths.append(path)
        raise failure_factory()

    with pytest.raises(ExtractionError, match="Could not extract the Word document"):
        extract_document(document_path, document_loader=fail_to_load)

    assert loaded_paths == [document_path], (
        "the injected loader should be reached for a valid package"
    )


def test_infrastructure_errors_names_every_translated_vendor_failure() -> None:
    """The extraction boundary should translate the full vendor failure set."""
    # The loader path and the package-size path share this tuple, so narrowing
    # it would silently let a vendor exception escape as a traceback.
    assert set(extractor.INFRASTRUCTURE_ERRORS) == {
        BadZipFile,
        EOFError,
        KeyError,
        OSError,
        PackageNotFoundError,
        RuntimeError,
        ValueError,
        XMLSyntaxError,
        XmlchemyError,
        zlib.error,
    }, "the boundary should translate every known vendor failure type"


def test_extract_document_rejects_oversized_packages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public API should reject oversized packages before loading them."""
    document_path = tmp_path / "oversized.docx"
    document_path.write_bytes(b"xx")
    monkeypatch.setattr(extractor, "MAX_INPUT_BYTES", 1)

    def fail_if_loaded(path: Path) -> typ.NoReturn:
        del path
        pytest.fail("oversized packages should be rejected before loader invocation")

    with pytest.raises(ExtractionError, match="too large"):
        extract_document(document_path, document_loader=fail_if_loaded)


@pytest.mark.parametrize(
    "case",
    [
        (
            "excessive-package-members",
            "MAX_PACKAGE_MEMBERS",
            2,
            "too many members",
        ),
        (
            "oversized-uncompressed",
            "MAX_UNCOMPRESSED_BYTES",
            1024,
            "uncompressed content is too large",
        ),
    ],
)
def test_extract_document_rejects_zip_resource_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, int, str],
) -> None:
    """ZIP metadata limits should reject unsafe packages before loading them."""
    fixture_name, limit_name, limit_value, message = case
    document_path = build_fixture(fixture_name, tmp_path / f"{fixture_name}.docx")
    monkeypatch.setattr(extractor, limit_name, limit_value)

    def fail_if_loaded(path: Path) -> typ.NoReturn:
        del path
        pytest.fail("resource-limit failures should occur before package loading")

    with pytest.raises(ExtractionError, match=message):
        extract_document(document_path, document_loader=fail_if_loaded)


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


def test_extract_document_normalizes_timezone_aware_metadata(tmp_path: Path) -> None:
    """Timezone-aware comment timestamps should convert to UTC."""
    document_path = build_fixture(
        "timezone-aware-comment",
        tmp_path / "timezone-aware-comment.docx",
    )

    result = extract_document(document_path)

    assert result.document.comments[0].timestamp == dt.datetime(
        2026,
        4,
        9,
        20,
        35,
        31,
        tzinfo=dt.UTC,
    ), "timezone-aware timestamps should normalize to their UTC instant"


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


def test_extract_document_preserves_inline_controls_in_ranges(
    tmp_path: Path,
) -> None:
    """Tabs and line breaks should survive inside and outside comment ranges."""
    document_path = build_fixture("inline-controls", tmp_path / "controls.docx")

    result = extract_document(document_path)
    fragments = result.document.blocks[0].fragments

    assert [fragment.text for fragment in fragments] == [
        "Outside\t\n",
        "Inside\t\n",
        "again\n",
        "After\t\n",
    ], "the extracted model should preserve tabs and both line-break elements"
    assert fragments[1].start_comment_ids == ("0",), (
        "the first control-bearing fragment should open the comment range"
    )
    assert fragments[2].end_comment_ids == ("0",), (
        "the final control-bearing fragment should close the comment range"
    )
    assert "{==Inside\t\nagain\n==}" in render_document(result.document), (
        "the renderer should preserve controls inside the CriticMarkup highlight"
    )
    assert render_document(result.document).startswith("Outside\t\n"), (
        "the renderer should preserve controls outside the comment range"
    )


def test_extract_paragraph_block_accumulates_many_end_markers() -> None:
    """A fragment should collect many end markers without repeated tuple copying."""
    document = Document()
    paragraph = document.add_paragraph("marked")
    for marker_id in range(128):
        marker = OxmlElement("w:commentRangeEnd")
        marker.set(qn("w:id"), str(marker_id))
        # The fixture requires an OOXML-only comment boundary marker.
        paragraph._p.append(marker)

    extraction = _extract_paragraph_block(paragraph)

    assert extraction.block.fragments[0].end_comment_ids == tuple(
        map(str, range(128))
    ), "the paragraph block should retain every end marker in document order"


@pytest.mark.parametrize("marker_name", ["commentRangeStart", "commentRangeEnd"])
def test_extract_paragraph_block_rejects_markers_without_identifiers(
    marker_name: str,
) -> None:
    """Malformed comment boundary markers should not leak low-level key errors."""
    document = Document()
    paragraph = document.add_paragraph("marked")
    marker = OxmlElement(f"w:{marker_name}")
    paragraph._p.append(marker)

    with pytest.raises(ExtractionError, match="missing its required w:id"):
        _extract_paragraph_block(paragraph)


@pytest.mark.parametrize(
    ("style_name", "expected_level"),
    [
        ("Heading 1", 1),
        ("Heading2", 2),
        ("Heading 3", 3),
        ("Heading 4", 4),
        ("Heading5", 5),
        ("Heading6", 6),
    ],
)
def test_heading_level_for_word_style(
    style_name: str,
    expected_level: int,
) -> None:
    """The python-docx adapter should map supported Word heading style names."""
    assert _heading_level_for_word_style(style_name) == expected_level, (
        "Word heading styles should map to their equivalent Markdown levels"
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


def _relocate_marker(
    source: Paragraph,
    destination: Paragraph,
    marker_name: str,
    *,
    index: int | None = None,
) -> None:
    """Move an OOXML comment boundary marker to a chosen child position.

    ``python-docx`` always writes both markers beside the commented runs, so
    the edge positions Word can itself produce only appear when a real marker
    is moved by hand. ``index`` picks the destination child offset; ``None``
    appends after the destination's last child.
    """
    marker = source._p.find(qn(f"w:{marker_name}"))
    assert marker is not None, f"the source should contain a w:{marker_name} marker"
    if index is None:
        destination._p.append(marker)
    else:
        destination._p.insert(index, marker)


def _assert_balanced_comment_markup(rendered: str, expected_count: int) -> None:
    """Assert that comment highlights open, close, and cite equally often."""
    counts = (
        rendered.count("{=="),
        rendered.count("==}"),
        rendered.count("{>>"),
    )
    assert counts == (expected_count,) * 3, (
        f"comment markup should balance at {expected_count}: {counts} in {rendered!r}"
    )


def _add_split_runs(paragraph: Paragraph, pieces: tuple[str, ...]) -> list[Run]:
    """Add one run per piece and return them in source order.

    Word splits a paragraph into runs wherever formatting changes, so a single
    token a reader sees as one word can arrive as several runs. The extractor
    must not treat each run as text that escaping can handle on its own.
    """
    return [paragraph.add_run(piece) for piece in pieces]


@pytest.mark.parametrize(
    ("name", "pieces", "expected"),
    [
        (
            "tag",
            ("<scr", "ipt>alert(1)</scr", "ipt", ">"),
            "&lt;script&gt;alert(1)&lt;/script&gt;",
        ),
        ("comment-opener", ("{", "==", "x", "==", "}"), r"\{==x==\}"),
        ("raw-comment", ("<scr", "ipt>x"), "&lt;script&gt;x"),
        ("block-opener", ("<!--", " unclosed"), "&lt;!-- unclosed"),
    ],
)
def test_extract_document_neutralizes_constructs_split_across_runs(
    tmp_path: Path,
    name: str,
    pieces: tuple[str, ...],
    expected: str,
) -> None:
    """A construct split across runs must not survive the render as live markup.

    The renderer escapes a group of adjacent runs as one unit precisely because
    the pieces reassemble: ``<scr`` and ``ipt>`` are each harmless alone, and an
    active tag after concatenation. This test drives the whole extraction and
    rendering path rather than the escape function alone, so a future change
    that escapes runs individually fails here.

    ``block-opener`` covers the unclosed HTML-block opener. ``<!--`` starts a
    CommonMark HTML block that runs to the next blank line, which would swallow
    the renderer's own CriticMarkup markers along with any later block.
    """
    document = Document()
    paragraph = document.add_paragraph()
    _add_split_runs(paragraph, pieces)
    document_path = tmp_path / f"split-{name}.docx"
    document.save(str(document_path))

    result = extract_document(document_path)
    rendered = render_document(result.document)

    assert result.warnings == (), "splitting text across runs is not itself a warning"
    assert rendered == expected, (
        "reassembled constructs should be neutralized as one logical string"
    )
    assert not _has_live_construct(rendered), (
        f"a consumer would act on the rendered output: {rendered!r}"
    )


def test_extract_document_separates_a_comment_boundary_from_adjacent_text(
    tmp_path: Path,
) -> None:
    """A comment boundary must split the escaped text, not be escaped through it.

    The commented run holds ``<scr`` and the following uncommented run holds
    ``ipt>``. Escaping them as one group would produce an active tag, so the
    boundary has to break the group even though both runs are adjacent. The
    rendered output therefore keeps ``ipt>`` outside the highlight, where it is
    inert on its own.
    """
    document = Document()
    paragraph = document.add_paragraph()
    commented_run, trailing_run = _add_split_runs(paragraph, ("<scr", "ipt>"))
    assert trailing_run is not None, "both runs should have been added"
    document.add_comment(commented_run, text="Split tag.", author="Probe")
    document_path = tmp_path / "split-tag-commented.docx"
    document.save(str(document_path))

    result = extract_document(document_path)
    rendered = render_document(result.document)

    assert result.warnings == (), "the boundary itself is well formed"
    # The highlight closes before the uncommented run, so the trailing text is
    # prose. Its '>' is inert because no '<' reaches it across the boundary.
    assert rendered.endswith("<<}ipt>"), (
        f"the uncommented run should render outside the highlight: {rendered!r}"
    )
    _assert_balanced_comment_markup(rendered, 1)
    prose = rendered.split("<<}", 1)[1]
    assert not _has_live_construct(prose), (
        f"prose outside the highlight should be inert: {prose!r}"
    )


def _has_live_construct(text: str) -> bool:
    """Report whether a consumer would treat part of ``text`` as markup.

    This delegates to the calibrated oracle in
    ``tests.support_renderer_oracles`` rather than counting substrings. Escape
    inserts a backslash before the closing brace of a range marker, so a raw
    substring search for the close marker finds nothing while a search for the
    opener still does: counting substrings would report a live construct in
    correctly escaped output. The oracle instead asks whether the backslash run
    before each character is odd, which is what a CommonMark reader does.
    """
    return composition_violation(text) is not None


def test_extract_document_anchors_trailing_start_on_next_block(
    tmp_path: Path,
) -> None:
    """A range start at a paragraph's end should anchor on the next block."""
    document = Document()
    first_paragraph = document.add_paragraph()
    first_paragraph.add_run("Alpha ")
    commented_run = first_paragraph.add_run("one")
    second_paragraph = document.add_paragraph("two")
    second_paragraph.add_run(" omega")
    document.add_comment(commented_run, text="Boundary probe.", author="Probe")
    # The start moves past the paragraph's final text while the end keeps a
    # following text fragment, as Word writes for a range opened at an edge.
    _relocate_marker(first_paragraph, first_paragraph, "commentRangeStart")
    _relocate_marker(first_paragraph, second_paragraph, "commentRangeEnd", index=1)
    document_path = tmp_path / "trailing-start.docx"
    document.save(str(document_path))

    result = extract_document(document_path)
    rendered = render_document(result.document)

    assert result.warnings == (), "an anchored boundary should not warn"
    second_block = result.document.blocks[1]
    assert second_block.fragments[0].start_comment_ids == ("0",), (
        "the trailing start should anchor on the next block's first fragment"
    )
    assert second_block.fragments[0].end_comment_ids == ("0",), (
        "the range end should keep closing on its own fragment"
    )
    _assert_balanced_comment_markup(rendered, 1)
    assert "Boundary probe." in rendered, "the comment body should render"


def test_extract_document_closes_leading_end_on_previous_block(
    tmp_path: Path,
) -> None:
    """A range end before a block's first text should close the previous block."""
    document = Document()
    first_paragraph = document.add_paragraph()
    first_paragraph.add_run("Alpha ")
    commented_run = first_paragraph.add_run("one")
    second_paragraph = document.add_paragraph("two omega")
    document.add_comment(commented_run, text="Boundary probe.", author="Probe")
    # The end moves ahead of the second paragraph's text, so no fragment of
    # that paragraph can carry it; the previous block's text must close it.
    _relocate_marker(first_paragraph, second_paragraph, "commentRangeEnd", index=0)
    document_path = tmp_path / "leading-end.docx"
    document.save(str(document_path))

    result = extract_document(document_path)
    rendered = render_document(result.document)

    assert result.warnings == (), "an anchored boundary should not warn"
    first_block = result.document.blocks[0]
    assert first_block.fragments[1].start_comment_ids == ("0",), (
        "the commented fragment should still open the range"
    )
    assert first_block.fragments[1].end_comment_ids == ("0",), (
        "the leading end should close on the previous block's last fragment"
    )
    _assert_balanced_comment_markup(rendered, 1)
    assert "Boundary probe." in rendered, "the comment body should render"


def test_extract_document_skips_empty_blocks_when_closing_leading_end(
    tmp_path: Path,
) -> None:
    """A leading end should close the nearest block that actually has text."""
    document = Document()
    first_paragraph = document.add_paragraph()
    first_paragraph.add_run("Alpha ")
    commented_run = first_paragraph.add_run("one")
    document.add_paragraph()
    third_paragraph = document.add_paragraph("three")
    document.add_comment(commented_run, text="Gap probe.", author="Probe")
    # An empty paragraph between the range's ends has no fragment to carry the
    # boundary, so the closing marker must reach past it to the commented text.
    _relocate_marker(first_paragraph, third_paragraph, "commentRangeEnd", index=0)
    document_path = tmp_path / "gap-end.docx"
    document.save(str(document_path))

    result = extract_document(document_path)
    rendered = render_document(result.document)

    assert result.warnings == (), "the boundary should still find a text fragment"
    assert result.document.blocks[1].fragments == (), (
        "the intervening paragraph should stay empty"
    )
    _assert_balanced_comment_markup(rendered, 1)
    assert "Gap probe." in rendered, "the comment body should render"


@pytest.mark.parametrize(
    ("marker_name", "index"),
    [
        ("commentRangeStart", None),
        ("commentRangeEnd", 0),
    ],
)
def test_extract_document_warns_for_unattachable_comment_markers(
    tmp_path: Path,
    marker_name: str,
    index: int | None,
) -> None:
    """Boundaries with no adjacent text should warn instead of vanishing."""
    document = Document()
    paragraph = document.add_paragraph("Alpha one")
    marker = OxmlElement(f"w:{marker_name}")
    marker.set(qn("w:id"), "0")
    if index is None:
        paragraph._p.append(marker)
    else:
        paragraph._p.insert(index, marker)
    document_path = tmp_path / f"unattached-{marker_name}.docx"
    document.save(str(document_path))

    result = extract_document(document_path)
    rendered = render_document(result.document)

    assert [warning.code for warning in result.warnings] == [
        "unattached-comment-marker"
    ], "an unattachable boundary should surface one stable warning code"
    assert "comment 0" in result.warnings[0].message, (
        "the warning should name the comment whose boundary could not attach"
    )
    _assert_balanced_comment_markup(rendered, 0)


def _remove_marker(paragraph: Paragraph, marker_name: str) -> None:
    """Delete a comment boundary marker from a paragraph."""
    marker = paragraph._p.find(qn(f"w:{marker_name}"))
    assert marker is not None, f"the paragraph should contain a w:{marker_name} marker"
    paragraph._p.remove(marker)


def _build_unclosed_range(path: Path) -> Path:
    """Build a document whose comment range opens and never closes."""
    document = Document()
    paragraph = document.add_paragraph()
    commented_run = paragraph.add_run("Alpha")
    document.add_comment(commented_run, text="Never closed.", author="Probe")
    _remove_marker(paragraph, "commentRangeEnd")
    document.save(str(path))
    return path


def _build_stray_end(path: Path) -> Path:
    """Build a document containing an end marker with no matching start."""
    document = Document()
    paragraph = document.add_paragraph("Alpha")
    stray = OxmlElement("w:commentRangeEnd")
    stray.set(qn("w:id"), "0")
    paragraph._p.append(stray)
    document.save(str(path))
    return path


def _build_end_with_unknown_id(path: Path) -> Path:
    """Build a document whose end marker names a comment it does not contain."""
    document = Document()
    paragraph = document.add_paragraph("Alpha")
    stray = OxmlElement("w:commentRangeEnd")
    stray.set(qn("w:id"), "99")
    paragraph._p.append(stray)
    document.save(str(path))
    return path


@pytest.mark.parametrize(
    ("name", "builder", "expected_reason", "expected_marker"),
    [
        (
            "unclosed-range",
            _build_unclosed_range,
            "never closes in this document",
            "commentRangeStart",
        ),
        (
            "stray-end",
            _build_stray_end,
            "no open comment range matches this end marker",
            "commentRangeEnd",
        ),
        (
            "unknown-identifier",
            _build_end_with_unknown_id,
            "no open comment range matches this end marker",
            "commentRangeEnd",
        ),
    ],
)
def test_extract_document_warns_for_unbalanced_comment_ranges(
    tmp_path: Path,
    *,
    name: str,
    builder: typ.Callable[[Path], Path],
    expected_reason: str,
    expected_marker: str,
) -> None:
    """Boundaries that cannot render as balanced CriticMarkup should warn.

    Rendering an unbalanced model either raises a ``KeyError`` or emits an
    unmatched delimiter, so the boundary is validated at extraction instead.
    The warning code is stable and user-facing, so it is asserted exactly
    rather than by substring.
    """
    document_path = builder(tmp_path / f"{name}.docx")

    result = extract_document(document_path)
    rendered = render_document(result.document)

    codes = [warning.code for warning in result.warnings]
    assert codes == ["unbalanced-comment-range"], (
        f"a dropped boundary should warn once with the stable code: {codes}"
    )
    message = result.warnings[0].message
    assert expected_marker in message, (
        f"the warning should name the dropped marker: {message!r}"
    )
    assert expected_reason in message, (
        f"the warning should explain why it was dropped: {message!r}"
    )
    # Whatever was dropped, the output must still be balanced CriticMarkup.
    _assert_balanced_comment_markup(rendered, 0)
    assert not _has_live_construct(rendered), (
        f"dropping a boundary must not leave live markup: {rendered!r}"
    )


@pytest.mark.parametrize("source_backslashes", range(4))
def test_extract_document_keeps_backslash_parity_before_brackets(
    tmp_path: Path,
    source_backslashes: int,
) -> None:
    """A source backslash run must never leave a bracket active.

    CommonMark treats a character as escaped only when the preceding backslash
    run is odd. The renderer doubles every source backslash before adding a
    single escape, so a source run of length ``n`` becomes ``2n + 1``: always
    odd, whatever the source contained. Without the doubling, an odd source run
    would become even and the added escape would cancel back to a live bracket.

    This is asserted through the whole extraction and rendering path, and it is
    asserted the way a consumer reads it. Counting whether a backslash appears
    before the bracket is not enough; the run has to be counted, because a
    single source backslash before a guarded bracket already produces a run of
    three.
    """
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("\\" * source_backslashes)
    paragraph.add_run("[run](https://example.org)")
    document_path = tmp_path / f"parity-{source_backslashes}.docx"
    document.save(str(document_path))

    result = extract_document(document_path)
    rendered = render_document(result.document)

    assert result.warnings == (), "backslashes in text are not a warning"
    bracket_positions = [
        index for index, character in enumerate(rendered) if character in "[]"
    ]
    assert bracket_positions, "the brackets should survive escaping as text"
    runs = [
        preceding_backslash_run(rendered, position) for position in bracket_positions
    ]
    assert all(run % 2 == 1 for run in runs), (
        f"every bracket needs an odd run before it, got {runs}: {rendered!r}"
    )
    # Only the first bracket follows the source run; the closing bracket is
    # preceded solely by the escape its own rule added.
    assert runs[0] == 2 * source_backslashes + 1, (
        f"doubling should leave an odd run, got {runs[0]}: {rendered!r}"
    )
    assert not has_active_bracket(rendered), (
        f"a consumer would act on a bracket in {rendered!r}"
    )


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
