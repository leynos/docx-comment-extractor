"""Render the normalized document model to Markdown with CriticMarkup."""

from __future__ import annotations

import datetime as dt
import re
import typing as typ

if typ.TYPE_CHECKING:
    from .models import Block, Comment, DocumentModel, Fragment

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

# The backslash is doubled before any other rule runs. Every rule below
# neutralizes its character by adding a single backslash in front of it, and a
# Markdown consumer reads a construct as escaped only when the run of
# backslashes before it is odd. Doubling first keeps that parity true: source
# backslashes arrive in even runs, so the added escape always makes the run
# odd. Without it, a source backslash would cancel the added escape and turn
# ``\[`` back into an active bracket.
MARKDOWN_TEXT_ESCAPE_SEQUENCES = (
    ("\\", "\\\\"),
    ("&", "&amp;"),
    ("[", r"\["),
    ("]", r"\]"),
)
# The first character class decides which *mid-line* constructs are protected:
# the HTML-block guard below only reaches an opener at a line start, so anything
# it omits here is left live wherever it sits inside a line. A processing
# instruction begins ``<?`` and never reached this pattern while the class held
# only letters, ``!`` and ``/``, so a mid-line ``<?php ?>`` survived escaping
# intact and a reader saw raw HTML. ``?`` is added for it. The CDATA opener is
# already covered through its ``!``.
RAW_HTML_PATTERN = re.compile(r"<(?P<content>[A-Za-z!?/][^>]*)>")

# ``RAW_HTML_PATTERN`` only guards a construct whose ``>`` is present. A line
# that *begins* with an HTML-block opener and never closes it is neutralized
# instead. CommonMark begins an HTML block at such a line for these prefixes,
# and the block runs to a blank line, so an unclosed ``<script`` or ``<!--``
# would otherwise swallow the renderer's own ``{==``/``{>>`` markers along with
# every later block. The same prefixes mid-line are inert, and escaping them
# anyway would be a visible change to text no reader would treat as markup.
#
# Four properties of the start condition decide the shape of this pattern.
#
# It applies at the start of *every* line, not merely at the start of the text,
# and a line ends at a carriage return just as it does at a line feed. A Word
# ``w:br`` or ``w:cr`` arrives as ``"\n"``, but a numeric character reference
# such as ``&#13;`` survives XML parsing as a real CR, so both endings are
# reachable. Python's ``(?m:^)`` recognizes only the line feed, which is why the
# anchor is spelled out here rather than written as a flag.
#
# It folds away any interleaving of whitespace and container markers before the
# opener, rather than only the three spaces of indentation a parser would allow.
# A parser folds the markers it has just seen on *this* line; this guard escapes
# one buffer at a time and cannot recompute a continuation line's content column,
# because that column depends on a container opened earlier and survived by the
# reader across the blank line ``render_document`` inserts between blocks.
# CommonMark retains a container across a blank line, so a list item and its
# continuation arrive as separate buffers and are still one item to the reader.
# Folding any run of markers and whitespace is the only per-buffer rule that
# reaches the continuation shapes.
#
# The over-approximation that buys is real and visible, and it is not confined
# to prose. A line a parser reads as indented code is guarded too, so
# ``"    <script"`` becomes ``"    &lt;script"``. CommonMark decodes an entity in
# prose but *not* inside a code span or a code block, so that renders as
# ``&amp;lt;script`` where the unguarded form rendered ``&lt;script``. The
# alternative -- a stateless guard that folds only three spaces -- leaves a
# reachable ``html_block`` live, which is the defect this pattern exists to
# prevent. Erring towards escaping is bounded and visible; erring towards
# missing is a swallowed document.
#
# The block tag names are matched case-insensitively, as the specification
# requires, while a declaration (``<!`` plus a letter) keeps the uppercase-only
# spelling the specification gives it. CommonMark restricts the *type 1* names
# (``pre``, ``script``, ``style``, ``textarea``) to the opening form alone, so
# ``</pre`` is inert at a line start while ``</div`` is live. Matching every name
# in both forms over-approximates by those four closing tags, which errs the same
# way as the prefix and costs less than a second branch.
#
# Only the leading bracket is consumed: the lookahead asserts which opener
# follows, and the named group then captures the ``<`` that the substitution
# replaces with an entity. The container prefix and the rest of the opener are
# preserved so the rendered text still reads as the source did.
HTML_BLOCK_OPENER = re.compile(
    r"(?:(?<=\n)|(?<=\r)|\A)"
    # Every alternative begins with a distinct character, so no run of markers
    # can be divided two ways and the repetition stays linear.
    r"(?P<prefix>(?:[ \t]|>|[-+*]|[0-9]{1,9}[.)])*)"
    r"(?="
    r"<!--"
    r"|<\?"
    # A declaration needs an uppercase letter. The bracket rule may already have
    # escaped the '[' of a CDATA opener, so both CDATA forms are matched too.
    r"|<!(?:[A-Z]|\\\[|\[CDATA\[)"
    r"|(?i:</?(?:address|article|aside|base|basefont|blockquote|body|caption"
    r"|center|col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption"
    r"|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe"
    r"|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p"
    r"|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr"
    r"|track|ul|pre|script|style|textarea)"
    # A tag name ends at whitespace, a tag terminator, or the end of the line.
    # ``<script&gt;`` is not an opener, because the entity continues the name.
    r"(?=\s|/?>|$))"
    r")(?P<bracket><)"
)


def _neutralize_html_block_openers(text: str) -> str:
    """Entity-escape every HTML-block opener no complete tag already covers.

    An opener with no ``>`` slips past ``RAW_HTML_PATTERN`` and, at a line
    start, begins an HTML block a CommonMark reader extends to the next blank
    line. Escaping the leading bracket stops the block, and the rest of the
    line renders as ordinary text.

    Guarding only unmatched openers keeps the documented accounting exact: the
    characters escaping consumes are the brackets of the tags it matched, plus
    one bracket per opener guarded here.
    """
    return HTML_BLOCK_OPENER.sub(r"\g<prefix>&lt;", text)


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
    for group in _contiguous_fragment_groups(block.fragments):
        parts.extend("{==" for _comment_id in group[0].start_comment_ids)
        group_text = "".join(fragment.text for fragment in group)
        parts.append(escape_criticmarkup_text(group_text))
        for comment_id in reversed(group[-1].end_comment_ids):
            comment = comment_lookup[comment_id]
            parts.extend((
                "==}",
                "{>>",
                escape_criticmarkup_text(format_comment_reference(comment)),
                "<<}",
            ))

    return "".join(parts)


def _contiguous_fragment_groups(
    fragments: tuple[Fragment, ...],
) -> typ.Iterator[tuple[Fragment, ...]]:
    """Group adjacent fragments that no comment boundary separates.

    Word splits a paragraph into runs wherever formatting changes, so text a
    reader sees as one token can arrive as several fragments. Escaping each
    fragment on its own would let them reassemble into a live construct that
    neither contains alone: ``<scr`` followed by ``ipt>`` renders an active
    HTML tag, and ``{`` followed by ``==`` renders a live CriticMarkup opener.

    A comment boundary still splits a group, because the markers rendered
    between two texts cannot be spanned by a delimiter formed from them.
    """
    group: list[Fragment] = []
    for fragment in fragments:
        if group and _separates_comment_boundary(group[-1], fragment):
            yield tuple(group)
            group = []
        group.append(fragment)
    if group:
        yield tuple(group)


def _separates_comment_boundary(previous: Fragment, current: Fragment) -> bool:
    """Report whether rendering ``previous`` emits markers before ``current``.

    Markers are emitted where a range ends or begins, so any boundary id on
    either side of the join puts rendered markup between the two texts. Text on
    opposite sides of that markup cannot reassemble into one construct.
    """
    return bool(previous.end_comment_ids or current.start_comment_ids)


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
    escaped = _escape_markdown_text(text)
    for source, replacement in CRITICMARKUP_ESCAPE_SEQUENCES:
        escaped = escaped.replace(source, replacement)
    return escaped


def _escape_markdown_text(text: str) -> str:
    """Neutralize raw HTML, Markdown-link syntax, and source backslashes."""
    escaped = text
    for source, replacement in MARKDOWN_TEXT_ESCAPE_SEQUENCES:
        escaped = escaped.replace(source, replacement)
    escaped = RAW_HTML_PATTERN.sub(r"&lt;\g<content>&gt;", escaped)
    # Last, so the entity it inserts is not escaped again by the '&' rule.
    return _neutralize_html_block_openers(escaped)


__all__ = [
    "escape_criticmarkup_text",
    "format_comment_reference",
    "render_document",
]
