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
        If the document loader cannot open or decode the Word package.

    """
    try:
        if _is_oversized_package(path):
            _raise_oversized_package()
        _validate_package_limits(path)
    except (
        BadZipFile,
        EOFError,
        KeyError,
        OSError,
        PackageNotFoundError,
        RuntimeError,
        ValueError,
        XMLSyntaxError,
        zlib.error,
    ) as error:
        _raise_extraction_error(error)
    document = _load_document_for_extraction(path, document_loader)
    comments = _extract_comments(document)
    blocks, warnings = _extract_blocks(document)
    return ExtractionResult(
        document=DocumentModel(blocks=tuple(blocks), comments=tuple(comments)),
        warnings=tuple(warnings),
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


def _load_document_for_extraction(
    path: Path,
    document_loader: DocumentLoader,
) -> WordDocument:
    """Load a package while translating known infrastructure failures."""
    try:
        return document_loader(path)
    except (
        BadZipFile,
        EOFError,
        KeyError,
        OSError,
        PackageNotFoundError,
        RuntimeError,
        ValueError,
        XMLSyntaxError,
        zlib.error,
    ) as error:
        _raise_extraction_error(error)


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

    for item in document.iter_inner_content():
        match item:
            case Paragraph():
                blocks.append(_extract_paragraph_block(item))
            case Table():
                warnings.append(
                    ExtractionWarning(
                        code="unsupported-block",
                        message="Encountered an unsupported top-level table block.",
                    )
                )

    return blocks, warnings


def _extract_paragraph_block(paragraph: Paragraph) -> Block:
    """Convert a Word paragraph and its comment boundaries into a block."""
    fragments: list[Fragment] = []
    fragment_end_ids: list[list[str]] = []
    pending_start_ids: list[str] = []

    for child in _iter_paragraph_xml_children(paragraph):
        match _local_name(child):
            case "commentRangeStart":
                pending_start_ids.append(_comment_id(child))
            case "commentRangeEnd":
                if fragment_end_ids:
                    fragment_end_ids[-1].append(_comment_id(child))
            case _:
                text = _extract_inline_text(child)
                if text:
                    fragments.append(
                        Fragment(
                            text=text,
                            start_comment_ids=tuple(pending_start_ids),
                        )
                    )
                    fragment_end_ids.append([])
                    pending_start_ids.clear()

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
