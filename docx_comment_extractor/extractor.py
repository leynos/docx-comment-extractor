"""Extract a normalized document model from a `.docx` file."""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
import typing as typ

from docx import Document

from .models import (
    Block,
    Comment,
    DocumentModel,
    ExtractionResult,
    ExtractionWarning,
    Fragment,
    heading_level_for_style,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from docx.comments import Comment as WordComment
    from docx.document import Document as WordDocument
    from docx.text.paragraph import Paragraph


class XmlElement(typ.Protocol):
    """The subset of OOXML element behaviour this extractor relies on."""

    tag: object
    text: str | None
    attrib: dict[object, object]

    def iter(self) -> typ.Iterator[XmlElement]:
        """Yield descendant elements in document order."""


def extract_document(path: Path) -> ExtractionResult:
    """Extract paragraphs, headings, comments, and warnings from `path`."""
    document = Document(str(path))
    comments = _extract_comments(document)
    blocks, warnings = _extract_blocks(document)
    return ExtractionResult(
        document=DocumentModel(blocks=tuple(blocks), comments=tuple(comments)),
        warnings=tuple(warnings),
    )


def _extract_comments(document: WordDocument) -> list[Comment]:
    return [_normalize_comment(comment) for comment in document.comments]


def _normalize_comment(comment: WordComment) -> Comment:
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
    paragraphs = [
        _normalize_inline_whitespace(paragraph.text)
        for paragraph in comment.paragraphs
        if _normalize_inline_whitespace(paragraph.text)
    ]
    return " / ".join(paragraphs)


def _normalize_timestamp(timestamp: dt.datetime | None) -> dt.datetime | None:
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=dt.UTC)
    return timestamp.astimezone(dt.UTC)


def _extract_blocks(
    document: WordDocument,
) -> tuple[list[Block], list[ExtractionWarning]]:
    blocks: list[Block] = []
    warnings: list[ExtractionWarning] = []
    paragraphs = iter(document.paragraphs)

    for child in document._body._element.iterchildren():
        local_name = _local_name(child)
        if local_name == "p":
            paragraph = next(paragraphs)
            blocks.append(_extract_paragraph_block(paragraph))
            continue
        if local_name == "tbl":
            warnings.append(
                ExtractionWarning(
                    code="unsupported-block",
                    message="Encountered an unsupported top-level table block.",
                )
            )
            continue
        if local_name == "sectPr":
            continue

    return blocks, warnings


def _extract_paragraph_block(paragraph: Paragraph) -> Block:
    fragments: list[Fragment] = []
    pending_start_ids: list[str] = []

    for child in paragraph._element.iterchildren():
        local_name = _local_name(child)
        if local_name == "commentRangeStart":
            pending_start_ids.append(_comment_id(child))
            continue

        if local_name == "commentRangeEnd":
            if fragments:
                fragments[-1] = dc.replace(
                    fragments[-1],
                    end_comment_ids=(
                        *fragments[-1].end_comment_ids,
                        _comment_id(child),
                    ),
                )
            continue

        text = _extract_inline_text(child)
        if text:
            fragments.append(
                Fragment(
                    text=text,
                    start_comment_ids=tuple(pending_start_ids),
                )
            )
            pending_start_ids.clear()

    style_name = paragraph.style.name if paragraph.style is not None else ""
    heading_level = heading_level_for_style(style_name)
    kind = "heading" if heading_level is not None else "paragraph"
    return Block(
        kind=kind,
        fragments=tuple(fragments),
        heading_level=heading_level,
    )


def _extract_inline_text(element: XmlElement) -> str:
    parts: list[str] = []
    for node in element.iter():
        local_name = _local_name(node)
        if local_name == "t":
            parts.append(_node_text(node))
            continue
        if local_name == "tab":
            parts.append("\t")
            continue
        if local_name in {"br", "cr"}:
            parts.append("\n")
    return "".join(parts)


def _comment_id(element: XmlElement) -> str:
    attribute_name = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}id"
    return str(element.attrib[attribute_name])


def _node_text(node: XmlElement) -> str:
    text = node.text
    if text is None:
        return ""
    return str(text)


def _normalize_inline_whitespace(text: str) -> str:
    return " ".join(text.split())


def _local_name(element: XmlElement) -> str:
    tag = str(element.tag)
    return tag.rsplit("}", 1)[-1]
