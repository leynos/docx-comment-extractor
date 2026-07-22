"""Render the normalized document model to Markdown with CriticMarkup."""

from __future__ import annotations

import datetime as dt

from .models import Block, Comment, DocumentModel
from .models import heading_level_for_style as heading_level_for_style

CRITICMARKUP_ESCAPE_SEQUENCES = (
    ("{++", r"\{++"),
    ("++}", r"++\}"),
    ("{--", r"\{--"),
    ("--}", r"--\}"),
    ("{~~", r"\{~~"),
    ("~>", r"~\>"),
    ("<~", r"<\~"),
    ("~~}", r"~~\}"),
    ("{==", r"\{=="),
    ("==}", r"==\}"),
    ("{>>", r"\{>>"),
    ("<<}", r"<<\}"),
)


def render_document(document: DocumentModel) -> str:
    """Render a document to Markdown with inline CriticMarkup comments.

    Parameters
    ----------
    document
        Normalized document model to render.

    Returns
    -------
    str
        Rendered Markdown without a forced trailing newline.

    """
    comment_lookup = {comment.comment_id: comment for comment in document.comments}
    rendered_blocks = [
        _render_block(block, comment_lookup) for block in document.blocks
    ]
    return "\n\n".join(rendered_blocks)


def _render_block(block: Block, comment_lookup: dict[str, Comment]) -> str:
    """Render one normalized block and its referenced comments."""
    prefix = ""
    if block.heading_level is not None:
        prefix = f"{'#' * block.heading_level} "

    parts: list[str] = [prefix]
    for fragment in block.fragments:
        parts.extend("{==" for _comment_id in fragment.start_comment_ids)
        parts.append(escape_criticmarkup_text(fragment.text))
        for comment_id in reversed(fragment.end_comment_ids):
            comment = comment_lookup[comment_id]
            parts.extend((
                "==}",
                "{>>",
                escape_criticmarkup_text(format_comment_reference(comment)),
                "<<}",
            ))

    return "".join(parts)


def format_comment_reference(comment: Comment) -> str:
    """Render a normalized comment as inline CriticMarkup comment text.

    Parameters
    ----------
    comment
        Comment whose available metadata and body should be rendered.

    Returns
    -------
    str
        Comment body prefixed by available author and timestamp metadata.

    """
    metadata: list[str] = []
    if comment.author:
        metadata.append(comment.author)
    if comment.timestamp is not None:
        metadata.append(_format_timestamp(comment.timestamp))

    if metadata and comment.body:
        return f"{', '.join(metadata)}: {comment.body}"
    if metadata:
        return ", ".join(metadata)
    return comment.body


def _format_timestamp(timestamp: dt.datetime) -> str:
    """Format a timestamp as a second-precision UTC ISO 8601 value."""
    normalized = timestamp.astimezone(dt.UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def escape_criticmarkup_text(text: str) -> str:
    """Escape literal CriticMarkup delimiters in text.

    Parameters
    ----------
    text
        Text whose CriticMarkup delimiters should be neutralized.

    Returns
    -------
    str
        Text with literal CriticMarkup delimiter sequences escaped.

    """
    escaped = text
    for source, replacement in CRITICMARKUP_ESCAPE_SEQUENCES:
        escaped = escaped.replace(source, replacement)
    return escaped


__all__ = [
    "escape_criticmarkup_text",
    "format_comment_reference",
    "heading_level_for_style",
    "render_document",
]
