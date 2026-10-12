"""Extract a normalized document model from a `.docx` file."""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
import re
import typing as typ
import zlib
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.exceptions import XmlchemyError
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError

from .models import (
    Block,
    Comment,
    DocumentModel,
    ExtractionResult,
    ExtractionWarning,
    Fragment,
)

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_PACKAGE_MEMBERS = 10_000
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
WORD_HEADING_STYLE_RE = re.compile(r"^Heading ?(?P<level>[1-6])$")

# Warning emitted when a comment boundary cannot be honored because the
# surrounding range structure is malformed; see ``_reconcile_comment_boundaries``.
_UNBALANCED_RANGE_CODE = "unbalanced-comment-range"

# Single definition of the vendor failures the extraction boundary translates
# into ``ExtractionError``. Keeping one tuple stops the package-size checks,
# the document loader, and the metadata and block extraction from drifting
# apart in what they consider a broken package.
#
# ``XmlchemyError`` is the vendor base for a document whose XML does not match
# the schema its code expects; ``InvalidXmlError`` is the subclass raised when
# a required attribute such as ``w:author`` is missing. ``python-docx`` 1.2.0
# reads comment attributes lazily, so those failures surface while comments
# are being normalized rather than while the package is being opened. It is
# not a ``ValueError``, so it needs naming here.
INFRASTRUCTURE_ERRORS = (
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
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from docx.comments import Comment as WordComment
    from docx.document import Document as WordDocument


class XmlElement(typ.Protocol):
    """The subset of OOXML element behaviour this extractor relies on."""

    tag: object
    text: str | None
    attrib: dict[object, object]

    def iter(self) -> typ.Iterator[XmlElement]:
        """Yield descendant elements in document order."""

    def iterchildren(self) -> typ.Iterator[XmlElement]:
        """Yield direct child elements in document order."""


class DocumentLoader(typ.Protocol):
    """Load a Word document from a filesystem path."""

    def __call__(self, path: Path) -> WordDocument:
        """Return the loaded document."""


class ExtractionError(Exception):
    """Report that a Word package could not be loaded for extraction."""


def _load_document(path: Path) -> WordDocument:
    """Load a Word package through the third-party document boundary."""
    return Document(str(path))


def extract_document(
    path: Path,
    *,
    document_loader: DocumentLoader = _load_document,
) -> ExtractionResult:
    """Extract paragraphs, headings, comments, and warnings from ``path``.

    Parameters
    ----------
    path
        Path to the Word ``.docx`` document to extract.
    document_loader
        Injectable package loader. The default opens ``path`` with
        ``python-docx``.

    Returns
    -------
    ExtractionResult
        The normalized document model and any non-fatal extraction warnings.

    Raises
    ------
    ExtractionError
        If the Word package cannot be opened, decoded, or read as the schema
        expects. The same error covers the deferred failures ``python-docx``
        raises while comment metadata is being read.

    """
    # One boundary covers the whole vendor interaction. ``python-docx`` reads
    # required comment attributes lazily, so a package that opens cleanly can
    # still raise while comments or paragraph XML are being read; translating
    # only the loader would let those escape as tracebacks past the CLI's
    # handled extraction-error path.
    try:
        if _is_oversized_package(path):
            _raise_oversized_package()
        _validate_package_limits(path)
        document = document_loader(path)
        comments = _extract_comments(document)
        blocks, warnings = _extract_blocks(document)
    except INFRASTRUCTURE_ERRORS as error:
        _raise_extraction_error(error)
    reconciled, boundary_warnings = _reconcile_comment_boundaries(blocks, comments)
    return ExtractionResult(
        document=DocumentModel(blocks=tuple(reconciled), comments=tuple(comments)),
        warnings=(*warnings, *boundary_warnings),
    )


def _is_oversized_package(path: Path) -> bool:
    """Return whether a readable package path exceeds the supported size limit."""
    return path.stat().st_size > MAX_INPUT_BYTES


def _validate_package_limits(path: Path) -> None:
    """Reject ZIP packages whose metadata exceeds supported resource limits."""
    with ZipFile(path) as package:
        members = package.infolist()
    if len(members) > MAX_PACKAGE_MEMBERS:
        message = "Input document ZIP package contains too many members."
        raise ExtractionError(message)
    if sum(member.file_size for member in members) > MAX_UNCOMPRESSED_BYTES:
        message = "Input document ZIP package uncompressed content is too large."
        raise ExtractionError(message)


def _raise_oversized_package() -> typ.NoReturn:
    """Stop extraction when the package exceeds its supported size limit."""
    message = "Input document is too large; maximum size is 20 MiB."
    raise ExtractionError(message)


def _raise_extraction_error(error: Exception) -> typ.NoReturn:
    """Translate an infrastructure exception at the extraction boundary."""
    message = "Could not extract the Word document."
    raise ExtractionError(message) from error


def _extract_comments(document: WordDocument) -> list[Comment]:
    """Normalize every comment exposed by the loaded Word document."""
    return [_normalize_comment(comment) for comment in document.comments]


def _normalize_comment(comment: WordComment) -> Comment:
    """Convert a Word comment into the internal comment model."""
    author = comment.author.strip() or None
    body = _flatten_comment_body(comment)
    timestamp = _normalize_timestamp(comment.timestamp)
    return Comment(
        comment_id=str(comment.comment_id),
        author=author,
        body=body,
        timestamp=timestamp,
    )


def _flatten_comment_body(comment: WordComment) -> str:
    """Flatten non-empty comment paragraphs into one rendering-safe line."""
    paragraphs = [
        _normalize_inline_whitespace(paragraph.text)
        for paragraph in comment.paragraphs
        if _normalize_inline_whitespace(paragraph.text)
    ]
    return " / ".join(paragraphs)


def _normalize_timestamp(timestamp: dt.datetime | None) -> dt.datetime | None:
    """Normalize a comment timestamp to Coordinated Universal Time."""
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=dt.UTC)
    return timestamp.astimezone(dt.UTC)


def _extract_blocks(
    document: WordDocument,
) -> tuple[list[Block], list[ExtractionWarning]]:
    """Extract supported top-level blocks and unsupported-block warnings."""
    blocks: list[Block] = []
    warnings: list[ExtractionWarning] = []
    pending_start_ids: tuple[str, ...] = ()

    for item in document.iter_inner_content():
        match item:
            case Paragraph():
                extraction = _extract_paragraph_block(item, pending_start_ids)
                pending_start_ids = extraction.trailing_start_ids
                # Leading ends close the previous block, so they must be
                # anchored before this paragraph's own block is appended.
                warnings.extend(
                    _attach_leading_ends(blocks, extraction.leading_end_ids)
                )
                blocks.append(extraction.block)
            case Table():
                warnings.append(
                    ExtractionWarning(
                        code="unsupported-block",
                        message="Encountered an unsupported top-level table block.",
                    )
                )

    # A start with no following text anywhere cannot open a range, so it is
    # reported rather than silently dropped.
    warnings.extend(
        _unattached_marker_warning("commentRangeStart", comment_id)
        for comment_id in pending_start_ids
    )
    return blocks, warnings


@dc.dataclass(frozen=True, slots=True)
class _ParagraphExtraction:
    """A paragraph's block plus comment boundaries its own text cannot hold.

    Attributes
    ----------
    block
        The paragraph converted to rendering fragments.
    trailing_start_ids
        Range starts left at the paragraph's end. A later block's first text
        fragment anchors them so a range can open across a paragraph break.
    leading_end_ids
        Range ends seen before the paragraph's first text. They close the
        nearest preceding text fragment so a range can end across a break.

    """

    block: Block
    trailing_start_ids: tuple[str, ...] = ()
    leading_end_ids: tuple[str, ...] = ()


def _extract_paragraph_block(
    paragraph: Paragraph,
    pending_start_ids: tuple[str, ...] = (),
) -> _ParagraphExtraction:
    """Convert a Word paragraph into a block and its outside-range boundaries."""
    fragments: list[Fragment] = []
    fragment_end_ids: list[list[str]] = []
    leading_end_ids: list[str] = []
    pending_ids = list(pending_start_ids)

    for child in _iter_paragraph_xml_children(paragraph):
        match _local_name(child):
            case "commentRangeStart":
                pending_ids.append(_comment_id(child))
            case "commentRangeEnd":
                if fragment_end_ids:
                    fragment_end_ids[-1].append(_comment_id(child))
                else:
                    leading_end_ids.append(_comment_id(child))
            case _:
                text = _extract_inline_text(child)
                if text:
                    fragments.append(
                        Fragment(
                            text=text,
                            start_comment_ids=tuple(pending_ids),
                        )
                    )
                    fragment_end_ids.append([])
                    pending_ids.clear()

    return _ParagraphExtraction(
        block=_build_paragraph_block(paragraph, fragments, fragment_end_ids),
        trailing_start_ids=tuple(pending_ids),
        leading_end_ids=tuple(leading_end_ids),
    )


def _build_paragraph_block(
    paragraph: Paragraph,
    fragments: list[Fragment],
    fragment_end_ids: list[list[str]],
) -> Block:
    """Assemble a paragraph block, closing fragments with their end markers."""
    style_name = paragraph.style.name if paragraph.style is not None else ""
    heading_level = _heading_level_for_word_style(style_name)
    kind = "heading" if heading_level is not None else "paragraph"
    return Block(
        kind=kind,
        fragments=tuple(
            dc.replace(fragment, end_comment_ids=tuple(end_ids))
            if end_ids
            else fragment
            for fragment, end_ids in zip(fragments, fragment_end_ids, strict=True)
        ),
        heading_level=heading_level,
    )


def _attach_leading_ends(
    blocks: list[Block],
    comment_ids: tuple[str, ...],
) -> list[ExtractionWarning]:
    """Close leading range ends on the nearest preceding block that has text."""
    # Attachment mutates ``blocks``, so run it as its own pass: a boundary is
    # only unattachable once every earlier block has been offered it.
    unattached_ids = [
        comment_id
        for comment_id in comment_ids
        if not _close_on_previous_block(blocks, comment_id)
    ]
    return [
        _unattached_marker_warning("commentRangeEnd", comment_id)
        for comment_id in unattached_ids
    ]


def _close_on_previous_block(blocks: list[Block], comment_id: str) -> bool:
    """Attach a range end to the last text fragment of an earlier block."""
    for index in range(len(blocks) - 1, -1, -1):
        block = blocks[index]
        if not block.fragments:
            continue
        last = block.fragments[-1]
        closed_last = dc.replace(
            last,
            end_comment_ids=(*last.end_comment_ids, comment_id),
        )
        blocks[index] = dc.replace(
            block,
            fragments=(*block.fragments[:-1], closed_last),
        )
        return True
    return False


class _RangeReconciler:
    """Validate comment boundaries while walking a model in document order.

    This release renders disjoint comment ranges one at a time, which is what
    Word writes and what the supplied sample contains. A boundary the model
    cannot render as balanced, fully resolved markup is dropped here and
    reported as a warning, so a malformed document degrades to literal text
    instead of raising a ``KeyError`` while rendering or emitting an unmatched
    delimiter.

    At most one range is open at any point. A start that would nest inside an
    open range, an end that matches no open range, and an open range that
    reaches the document's end all fail that test. Rejection is recorded rather
    than raised because one malformed marker should not discard an otherwise
    readable document; the warnings keep the loss visible.
    """

    def __init__(self, known_ids: set[str]) -> None:
        """Track range state against the identifiers the model actually has."""
        self._known_ids = known_ids
        self._open_id: str | None = None
        self._open_position: tuple[int, int] | None = None
        self.warnings: list[ExtractionWarning] = []

    def reconcile_fragment(
        self,
        fragment: Fragment,
        *,
        position: tuple[int, int],
    ) -> Fragment:
        """Return the fragment with only its renderable boundaries kept."""
        starts = self._accept_starts(position, fragment.start_comment_ids)
        ends = self._accept_ends(fragment.end_comment_ids)
        if starts == fragment.start_comment_ids and ends == fragment.end_comment_ids:
            return fragment
        return dc.replace(
            fragment,
            start_comment_ids=starts,
            end_comment_ids=ends,
        )

    def unclosed_range(self) -> tuple[str, tuple[int, int]] | None:
        """Report an open range and return where its start marker must go."""
        if self._open_id is None or self._open_position is None:
            return None
        comment_id = self._open_id
        position = self._open_position
        self._reject(
            "commentRangeStart",
            comment_id,
            "the range never closes in this document",
        )
        return comment_id, position

    def _accept_starts(
        self,
        position: tuple[int, int],
        comment_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Return the start markers that can open a renderable range."""
        accepted: list[str] = []
        for comment_id in comment_ids:
            if comment_id not in self._known_ids:
                self._reject(
                    "commentRangeStart",
                    comment_id,
                    "the document contains no comment with that identifier",
                )
            elif self._open_id is not None:
                self._reject(
                    "commentRangeStart",
                    comment_id,
                    "the range would nest inside an already-open comment range",
                )
            else:
                self._open_id = comment_id
                self._open_position = position
                accepted.append(comment_id)
        return tuple(accepted)

    def _accept_ends(self, comment_ids: tuple[str, ...]) -> tuple[str, ...]:
        """Return the end markers that close the currently open range."""
        accepted: list[str] = []
        for comment_id in comment_ids:
            if comment_id != self._open_id:
                self._reject(
                    "commentRangeEnd",
                    comment_id,
                    "no open comment range matches this end marker",
                )
                continue
            self._open_id = None
            self._open_position = None
            accepted.append(comment_id)
        return tuple(accepted)

    def _reject(self, marker_name: str, comment_id: str, reason: str) -> None:
        """Record one dropped boundary as a stable warning."""
        self.warnings.append(_unbalanced_range_warning(marker_name, comment_id, reason))


def _reconcile_comment_boundaries(
    blocks: list[Block],
    comments: list[Comment],
) -> tuple[list[Block], list[ExtractionWarning]]:
    """Drop comment boundaries that cannot render as balanced CriticMarkup.

    ``python-docx`` and Word can both produce marker sequences this release's
    rendering model cannot express: a marker naming a comment the document does
    not contain, an end with no matching start, or a range that opens and never
    closes. Rendering such a model raises a ``KeyError`` or emits an unmatched
    delimiter, so every boundary is validated here, at the extraction boundary,
    where the failure can be reported instead of thrown.

    Nested and overlapping ranges are outside the supported range model, so a
    start that would nest is rejected like any other unrenderable boundary.
    """
    reconciler = _RangeReconciler({comment.comment_id for comment in comments})
    fragments_by_block: list[list[Fragment]] = [
        [
            reconciler.reconcile_fragment(
                fragment,
                position=(block_index, fragment_index),
            )
            for fragment_index, fragment in enumerate(block.fragments)
        ]
        for block_index, block in enumerate(blocks)
    ]
    _drop_unclosed_start(fragments_by_block, reconciler.unclosed_range())
    reconciled = [
        dc.replace(block, fragments=tuple(fragments))
        for block, fragments in zip(blocks, fragments_by_block, strict=True)
    ]
    return reconciled, reconciler.warnings


def _drop_unclosed_start(
    fragments_by_block: list[list[Fragment]],
    unclosed: tuple[str, tuple[int, int]] | None,
) -> None:
    """Remove the start marker of a range that never reached its end."""
    if unclosed is None:
        return
    comment_id, (block_index, fragment_index) = unclosed
    fragment = fragments_by_block[block_index][fragment_index]
    fragments_by_block[block_index][fragment_index] = dc.replace(
        fragment,
        start_comment_ids=tuple(
            kept for kept in fragment.start_comment_ids if kept != comment_id
        ),
    )


def _unbalanced_range_warning(
    marker_name: str,
    comment_id: str,
    reason: str,
) -> ExtractionWarning:
    """Warn when a comment boundary cannot render as balanced range markup."""
    message = f"Ignoring a {marker_name} for comment {comment_id}: {reason}."
    return ExtractionWarning(code=_UNBALANCED_RANGE_CODE, message=message)


def _unattached_marker_warning(
    marker_name: str,
    comment_id: str,
) -> ExtractionWarning:
    """Warn when a comment boundary has no adjacent text to anchor on."""
    message = (
        f"Ignoring a {marker_name} for comment {comment_id}: "
        "no adjacent text exists to anchor the boundary."
    )
    return ExtractionWarning(code="unattached-comment-marker", message=message)


def _iter_paragraph_xml_children(paragraph: Paragraph) -> typ.Iterator[XmlElement]:
    """Yield the private OOXML children behind a public paragraph object."""
    element = typ.cast(
        "XmlElement",
        object.__getattribute__(paragraph, "_element"),
    )
    return element.iterchildren()


def _extract_inline_text(element: XmlElement) -> str:
    """Collect text, tabs, and line breaks from an inline XML element."""
    parts: list[str] = []
    for node in element.iter():
        match _local_name(node):
            case "t":
                parts.append(_node_text(node))
            case "tab":
                parts.append("\t")
            case "br" | "cr":
                parts.append("\n")
    return "".join(parts)


def _comment_id(element: XmlElement) -> str:
    """Read a comment identifier from an OOXML range marker."""
    attribute_name = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}id"
    comment_id = element.attrib.get(attribute_name)
    if comment_id is None:
        message = "Comment range marker is missing its required w:id attribute."
        raise ExtractionError(message)
    return str(comment_id)


def _node_text(node: XmlElement) -> str:
    """Return an XML node's text as a non-optional string."""
    text = node.text
    if text is None:
        return ""
    return str(text)


def _normalize_inline_whitespace(text: str) -> str:
    """Collapse runs of inline whitespace to single spaces."""
    return " ".join(text.split())


def _local_name(element: XmlElement) -> str:
    """Return an XML element name without its namespace."""
    tag = str(element.tag)
    return tag.rsplit("}", 1)[-1]


def _heading_level_for_word_style(style_name: str) -> int | None:
    """Map a python-docx Word heading style name to a Markdown level."""
    match = WORD_HEADING_STYLE_RE.fullmatch(style_name)
    if match is None:
        return None
    return int(match.group("level"))
