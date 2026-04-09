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
    """Normalized Word comment metadata and body text."""

    comment_id: str
    author: str | None
    body: str
    timestamp: dt.datetime | None = None


@dc.dataclass(frozen=True, slots=True)
class Fragment:
    """A text fragment plus comment boundaries attached to it."""

    text: str
    start_comment_ids: tuple[str, ...] = ()
    end_comment_ids: tuple[str, ...] = ()


@dc.dataclass(frozen=True, slots=True)
class Block:
    """A document block in source order."""

    kind: BlockKind
    fragments: tuple[Fragment, ...]
    heading_level: int | None = None


@dc.dataclass(frozen=True, slots=True)
class DocumentModel:
    """A normalized document ready for Markdown rendering."""

    blocks: tuple[Block, ...]
    comments: tuple[Comment, ...]


@dc.dataclass(frozen=True, slots=True)
class ExtractionWarning:
    """A non-fatal extraction warning."""

    code: str
    message: str


@dc.dataclass(frozen=True, slots=True)
class ExtractionResult:
    """The extracted document plus any non-fatal warnings."""

    document: DocumentModel
    warnings: tuple[ExtractionWarning, ...]


def heading_level_for_style(style_name: str) -> int | None:
    """Return the Markdown ATX level for a Word heading style name."""
    match = HEADING_STYLE_RE.fullmatch(style_name)
    if match is None:
        return None
    return int(match.group("level"))
