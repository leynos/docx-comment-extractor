"""Immutable data structures for comment extraction and rendering."""

from __future__ import annotations

import dataclasses as dc
import re
import typing as typ

if typ.TYPE_CHECKING:
    import datetime as dt

HEADING_STYLE_RE = re.compile(r"^Heading ?(?P<level>[1-6])$")

BlockKind = typ.Literal["heading", "paragraph"]


@dc.dataclass(frozen=True, slots=True)
class Comment:
    """Normalized Word comment metadata and body text.

    Attributes
    ----------
    comment_id
        Word comment identifier used to pair the comment with its anchors.
    author
        Normalized author name, or ``None`` when no author is available.
    body
        Normalized and flattened comment body text.
    timestamp
        UTC-normalized comment timestamp, or ``None`` when unavailable.

    """

    comment_id: str
    author: str | None
    body: str
    timestamp: dt.datetime | None = None


@dc.dataclass(frozen=True, slots=True)
class Fragment:
    """A text fragment plus comment boundaries attached to it.

    Attributes
    ----------
    text
        Inline document text in source order.
    start_comment_ids
        Comment identifiers whose ranges open at this fragment.
    end_comment_ids
        Comment identifiers whose ranges close at this fragment.

    """

    text: str
    start_comment_ids: tuple[str, ...] = ()
    end_comment_ids: tuple[str, ...] = ()


@dc.dataclass(frozen=True, slots=True)
class Block:
    """A document block in source order.

    Attributes
    ----------
    kind
        Rendering kind for the paragraph or heading block.
    fragments
        Ordered inline fragments contained by the block.
    heading_level
        Markdown heading level, or ``None`` for a body paragraph.

    """

    kind: BlockKind
    fragments: tuple[Fragment, ...]
    heading_level: int | None = None


@dc.dataclass(frozen=True, slots=True)
class DocumentModel:
    """A normalized document ready for Markdown rendering.

    Attributes
    ----------
    blocks
        Renderable document blocks in source order.
    comments
        Normalized comments referenced by fragment boundaries.

    """

    blocks: tuple[Block, ...]
    comments: tuple[Comment, ...]


@dc.dataclass(frozen=True, slots=True)
class ExtractionWarning:
    """A non-fatal extraction warning.

    Attributes
    ----------
    code
        Stable machine-readable warning category.
    message
        User-facing explanation of the extraction limitation.

    """

    code: str
    message: str


@dc.dataclass(frozen=True, slots=True)
class ExtractionResult:
    """The extracted document plus any non-fatal warnings.

    Attributes
    ----------
    document
        Normalized document model produced by extraction.
    warnings
        Non-fatal issues encountered while extracting the document.

    """

    document: DocumentModel
    warnings: tuple[ExtractionWarning, ...]


def heading_level_for_style(style_name: str) -> int | None:
    """Return the Markdown ATX level for a Word heading style name."""
    match = HEADING_STYLE_RE.fullmatch(style_name)
    if match is None:
        return None
    return int(match.group("level"))
