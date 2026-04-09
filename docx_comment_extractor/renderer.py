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
    """Render `document` to Markdown with inline CriticMarkup comments."""
    comment_lookup = {comment.comment_id: comment for comment in document.comments}
    rendered_blocks = [
        _render_block(block, comment_lookup) for block in document.blocks
    ]
    return "\n\n".join(rendered_blocks)


def _render_block(block: Block, comment_lookup: dict[str, Comment]) -> str:
    prefix = ""
    if block.heading_level is not None:
        prefix = f"{'#' * block.heading_level} "

    parts = [prefix]
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
    """Render a normalized comment as inline CriticMarkup comment text."""
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
    normalized = timestamp.astimezone(dt.UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def escape_criticmarkup_text(text: str) -> str:
    """Escape literal CriticMarkup delimiters in `text`."""
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
