"""Property-based tests for comment-range reconstruction and escaping.

The oracles here are deliberately independent of the code under test. They
restate the contract documented in ``docs/comment-extraction-design.md`` rather
than replaying the rendering algorithm, so a defect shared between the
implementation and a copy of it cannot hide behind the test.

The suite is a bounded search, not a proof, and its domain excludes several
cases on purpose. Source newlines are absent, because the renderer receives
text the extractor has already normalized to one line. Comment bodies are fixed
identifiers rather than arbitrary text. Authors and timestamps are absent. The
extractor is not exercised here; only the renderer and its escape functions are.
Malformed, nested, and overlapping ranges are not generated, because the
supported range model is a sequence of disjoint ranges. Coverage of those
exclusions lives in ``tests/unit/test_extractor.py`` and
``tests/unit/test_cli.py`` instead.
"""

from __future__ import annotations

import re
import typing as typ

from hypothesis import example, given
from hypothesis import strategies as st

from docx_comment_extractor.models import Block, Comment, DocumentModel, Fragment
from docx_comment_extractor.renderer import escape_criticmarkup_text, render_document
from tests.support_renderer_oracles import (
    LIVE_RAW_HTML,
    RANGE_MARKERS,
    RAW_HTML_TAG,
    active_delimiter_count,
    composition_violation,
    consumed_spans,
    guarded_opener_count,
    has_active_bracket,
)

if typ.TYPE_CHECKING:
    from hypothesis.strategies import DrawFn

# The documented escape table, restated here so that editing the module's table
# cannot silently redefine what these properties consider correct.
DOCUMENTED_ESCAPES = (
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

LIVE_AMPERSAND = re.compile(r"&(?!amp;|lt;|gt;)")

# Characters used by some escape sequence, the raw-HTML pattern, the Markdown
# link escapes, or the backslash rule. Text built without them cannot contain a
# construct that escaping would touch.
REACTIVE_CHARACTERS = "&[]<>{}~+=-\\"

# Inert characters are the ones escaping leaves alone. The accent, arrow, CJK,
# and emoji characters keep the generated domain from being ASCII-only, so the
# Unicode claim in the design document is exercised rather than asserted.
INERT_CHARACTERS = "".join(
    character
    for character in (
        " abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        "\t.,;:!?'\"()@#$%^*|`/éüñ→字🙂"
    )
    if character not in REACTIVE_CHARACTERS
)

# Document text additionally avoids '@' so that generated comment bodies stay
# identifiable in the rendered output. Every character is inert, so the
# reconstruction oracle can compare rendered text against source text without
# inverting any escaping.
DOCUMENT_TEXT_CHARACTERS = "".join(
    character for character in INERT_CHARACTERS if character != "@"
)

# Reconstruction inputs stay small enough for the default Hypothesis profile to
# run comfortably inside a routine test job.
MAX_COMMENT_RANGES = 3
MAX_RANGE_SPAN = 3
MAX_GAP_FRAGMENTS = 2
MAX_BLOCK_CUTS = 3

COMMENT_REFERENCE = re.compile(r"\{>>(?P<body>.*?)<<\}")

HTML_SAMPLES = (
    "<script>alert(1)</script>",
    '<a href="x">',
    "<!--",
    "</div>",
    "<b>",
    "<img src=x>",
)
MARKDOWN_LINK_SAMPLES = ("[run](javascript:alert(1))", "[link](x)", "](", "[[")
# Fragments carrying a backslash already, as an already-escaped input would.
BACKSLASH_SAMPLES = ("\\", r"\{++", r"~\>", r"\[", "&lt;")

# Each documented delimiter is also pinned as a named example below. Drawing it
# from the generator is enough in practice, but not on every run, and a property
# whose sensitivity depends on the draw can pass a defective build by luck.
# Pinning keeps each single-rule mutation detectable on every run.
PINNED_DELIMITERS = tuple(source for source, _replacement in DOCUMENTED_ESCAPES)
# A delimiter inside a raw HTML span exercises the order of the two escape
# layers: Markdown escaping consumes the span before the delimiter is reached.
PINNED_ORDER_SENSITIVE = "<a~>"
# A bare '&' reaches the ampersand rule without arriving entity-shaped.
PINNED_AMPERSANDS = ("&", "&x", "&amp;")
# A source backslash immediately before a bracket: the added escape must leave
# an odd-length backslash run, or a CommonMark reader sees an active link.
PINNED_BACKSLASH_LINK = r"\[run\](https://example.org)"
# Unmatched HTML-block openers a CommonMark reader promotes to a block at a line
# start. Each is a variant an earlier guard missed: an indented opener, an
# uppercase one, one after a Word line break, and one split across Word runs, so
# that the reassembled text is never the string either fragment held.
PINNED_UNMATCHED_OPENERS = (
    " <script",
    "<SCRIPT",
    "safe\n<script",
    "safe\n<!--",
    "<scr\nipt",
    " <script\n{==x==}{>>note<<}",
)

# Composition text is drawn from the reactive characters themselves, so that
# adjacent fragments can reassemble a construct neither fragment contains. The
# line-feed is included because a Word line break reaches the renderer as
# ``"\n"`` and leaves a real line start behind, which is where CommonMark
# promotes an unmatched HTML-block opener into a block that swallows the
# renderer's own markers. The letters are the ones that spell a block tag name.
COMPOSITION_CHARACTERS = "<>[](){}=+~-\\/!&\nabcdeilnoprstuvxyz"


def _assert_no_live_construct(escaped: str) -> None:
    """Fail if a Markdown or CriticMarkup consumer could act on ``escaped``."""
    match = LIVE_RAW_HTML.search(escaped)
    assert match is None, (
        f"a raw HTML tag survived escaping: {match and match.group()!r}"
    )
    assert LIVE_AMPERSAND.search(escaped) is None, "a bare ampersand survived escaping"
    for delimiter in PINNED_DELIMITERS:
        assert active_delimiter_count(escaped, delimiter) == 0, (
            f"a live {delimiter!r} survived escaping"
        )
    assert not has_active_bracket(escaped), (
        "an active Markdown bracket survived escaping"
    )


def strip_documented_escapes(text: str) -> str:
    """Reverse every documented escape without reusing the module's table.

    Order matters. The renderer escapes '&' before it neutralizes raw HTML, so
    an '&lt;' in the output came from a literal '<' and must be decoded before
    the '&amp;' rule runs. The backslash doubling has no inverse here: an input
    already containing an escape sequence cannot be recovered unambiguously,
    which is why the round-trip property draws from an alphabet without
    backslashes.
    """
    for source, replacement in reversed(DOCUMENTED_ESCAPES):
        text = text.replace(replacement, source)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return text.replace(r"\[", "[").replace(r"\]", "]")


def _tag_content() -> st.SearchStrategy[str]:
    """Generate tag content that may itself contain delimiter sequences."""
    return st.lists(
        st.one_of(
            st.sampled_from([source for source, _replacement in DOCUMENTED_ESCAPES]),
            st.text(alphabet=INERT_CHARACTERS, max_size=2),
        ),
        max_size=3,
    ).map("".join)


def _wrap_tag(head: str, content: str) -> str:
    """Wrap content in an opener the raw-HTML pattern recognizes."""
    return f"<{head}{content}>"


def _raw_html_tags() -> st.SearchStrategy[str]:
    """Generate raw HTML tags whose content can carry delimiters.

    A tag span runs from '<' to the next '>', and the renderer protects that
    whole span as a unit. Putting a delimiter inside the span is what makes the
    relative order of the Markdown and CriticMarkup escape layers observable:
    Markdown escaping consumes the span first, so a delimiter inside it is
    never seen by the CriticMarkup layer.
    """
    return st.builds(
        _wrap_tag,
        st.sampled_from(("a", "b", "!", "/", "img")),
        _tag_content(),
    )


def _escapable_fragments() -> st.SearchStrategy[str]:
    """Generate fragments built only from constructs with a documented escape."""
    return st.one_of(
        st.sampled_from([source for source, _replacement in DOCUMENTED_ESCAPES]),
        st.sampled_from(HTML_SAMPLES),
        _raw_html_tags(),
        st.sampled_from(MARKDOWN_LINK_SAMPLES),
        # Bare and entity-shaped ampersands, so the '&' escape rule is reached
        # on its own and not only through an '&lt;' sample.
        st.sampled_from(("&", "&amp;", "&lt;", "&x")),
        st.text(alphabet=INERT_CHARACTERS, max_size=4),
    )


def _untrusted_fragments() -> st.SearchStrategy[str]:
    """Generate escapable fragments, including text already carrying escapes."""
    return st.one_of(
        _escapable_fragments(),
        st.sampled_from(BACKSLASH_SAMPLES),
    )


def document_text() -> st.SearchStrategy[str]:
    """Generate text the renderer passes through unescaped."""
    return st.text(alphabet=DOCUMENT_TEXT_CHARACTERS, max_size=4)


def composition_text() -> st.SearchStrategy[str]:
    """Generate short fragments that can reassemble a live construct.

    Fragments are short and built from the reactive characters themselves, so
    the generator produces the boundary shapes that matter: ``"<scr"`` beside
    ``"ipt>"``, ``"{"`` beside ``"=="``, and backslash runs split from the
    bracket they precede.
    """
    return st.text(alphabet=COMPOSITION_CHARACTERS, max_size=4)


def _comment_id(index: int) -> str:
    """Return the identifier generated for a comment range."""
    return f"c{index}"


def _comment_body(index: int) -> str:
    """Return a body that cannot collide with generated document text."""
    return f"@a{index}@"


def _heading_prefix(heading_level: int | None) -> str:
    """Return the Markdown prefix a heading block contributes."""
    if heading_level is None:
        return ""
    return "#" * heading_level + " "


def _lay_out_ownership(spans: list[int], gaps: list[int]) -> list[int | None]:
    """Lay disjoint comment ranges over fragment slots in document order."""
    owners: list[int | None] = [None] * gaps[0]
    for index, span in enumerate(spans):
        owners.extend([index] * span)
        owners.extend([None] * gaps[index + 1])
    # Two trailing unowned slots keep an interior block boundary available even
    # when the document has no comment ranges at all.
    owners.extend([None, None])
    return owners


def _fragment(owners: list[int | None], texts: list[str], slot: int) -> Fragment:
    """Build one fragment, attaching boundaries at the range's own edges."""
    owner = owners[slot]
    if owner is None:
        return Fragment(text=texts[slot])
    first = owners.index(owner)
    last = len(owners) - 1 - owners[::-1].index(owner)
    return Fragment(
        text=texts[slot],
        start_comment_ids=(_comment_id(owner),) if slot == first else (),
        end_comment_ids=(_comment_id(owner),) if slot == last else (),
    )


def _build_blocks(
    owners: list[int | None],
    bounds: list[int],
    texts: list[str],
    levels: list[int | None],
) -> list[Block]:
    """Group fragment slots into blocks at the drawn boundary offsets."""
    return [
        Block(
            kind="heading" if levels[index] is not None else "paragraph",
            fragments=tuple(
                _fragment(owners, texts, slot)
                for slot in range(bounds[index], bounds[index + 1])
            ),
            heading_level=levels[index],
        )
        for index in range(len(bounds) - 1)
    ]


def _empty_block(level: int | None) -> Block:
    """Build a block with no fragments, as the model permits."""
    return Block(
        kind="heading" if level is not None else "paragraph",
        fragments=(),
        heading_level=level,
    )


@st.composite
def document_models(
    draw: DrawFn,
    text: st.SearchStrategy[str] | None = None,
) -> DocumentModel:
    """Generate a bounded model whose comment ranges are balanced.

    Every range spans one or more consecutive fragment slots, so no two ranges
    nest or overlap. That stays inside the supported range semantics recorded
    in the design document: the reference document contains neither.

    ``text`` overrides the fragment alphabet. The default is inert text, which
    the reconstruction oracle can compare without inverting escapes; the
    composition property substitutes reactive text and uses its own oracles.
    """
    text_strategy = document_text() if text is None else text
    count = draw(st.integers(min_value=0, max_value=MAX_COMMENT_RANGES))
    spans = draw(
        st.lists(st.integers(1, MAX_RANGE_SPAN), min_size=count, max_size=count)
    )
    gaps = draw(
        st.lists(
            st.integers(0, MAX_GAP_FRAGMENTS), min_size=count + 1, max_size=count + 1
        )
    )
    owners = _lay_out_ownership(spans, gaps)
    cuts = sorted(
        draw(
            st.sets(
                st.integers(min_value=1, max_value=len(owners) - 1),
                max_size=MAX_BLOCK_CUTS,
            )
        )
    )
    bounds = [0, *cuts, len(owners)]
    texts = draw(st.lists(text_strategy, min_size=len(owners), max_size=len(owners)))
    levels = draw(
        st.lists(
            st.one_of(st.none(), st.integers(min_value=1, max_value=6)),
            min_size=len(bounds) - 1,
            max_size=len(bounds) - 1,
        )
    )
    blocks = _build_blocks(owners, bounds, texts, levels)
    if draw(st.booleans()):
        blocks.insert(
            draw(st.integers(min_value=0, max_value=len(blocks))),
            _empty_block(draw(st.sampled_from((None, 2)))),
        )
    return DocumentModel(
        blocks=tuple(blocks),
        comments=tuple(
            Comment(
                comment_id=_comment_id(index),
                author=None,
                body=_comment_body(index),
            )
            for index in range(count)
        ),
    )


def _strip_range_markers(rendered: str) -> str:
    """Remove every CriticMarkup delimiter the renderer emits."""
    for marker in RANGE_MARKERS:
        rendered = rendered.replace(marker, "")
    return rendered


def _expected_plain_text(document: DocumentModel) -> str:
    """Rebuild the text the renderer must reproduce, in source order."""
    return "\n\n".join(
        _heading_prefix(block.heading_level)
        + "".join(fragment.text for fragment in block.fragments)
        for block in document.blocks
    )


def _iter_range_tokens(rendered: str) -> typ.Iterator[tuple[str, int, str]]:
    """Yield ('open'|'close', offset, comment body) for each rendered range."""
    index = 0
    while index < len(rendered):
        if rendered.startswith("{==", index):
            yield "open", index, ""
            index += 3
        elif rendered.startswith("==}", index):
            match = COMMENT_REFERENCE.match(rendered, index + 3)
            assert match is not None, "a range close should carry a comment reference"
            yield "close", index, match.group("body")
            index = match.end()
        else:
            index += 1


def _highlight_interiors(rendered: str) -> list[str]:
    """Return the exact text each rendered highlight encloses.

    Opening a highlight while another is open is reported rather than absorbed:
    the generated ranges are disjoint, so nesting would mean the renderer
    emitted a structure the model never described.
    """
    interiors: list[str] = []
    opening: int | None = None
    index = 0
    while index < len(rendered):
        if rendered.startswith("{==", index):
            assert opening is None, "a highlight opened inside another highlight"
            opening = index + 3
            index += 3
        elif rendered.startswith("==}", index):
            assert opening is not None, "a highlight closed without a matching open"
            interiors.append(rendered[opening:index])
            opening = None
            index += 3
        else:
            index += 1
    assert opening is None, "every opened highlight should close"
    return interiors


def _expected_highlight_spans(document: DocumentModel) -> list[tuple[str, str]]:
    """Map each comment to the source text its highlight must enclose.

    This walks the model rather than the rendered output. It is the mapping the
    marker-order oracle lacks: two renderings can both preserve the source text
    and close every range once, yet highlight the wrong span. Blank-line joins
    and heading prefixes count as part of a highlight only when a range is
    already open as the renderer reaches them, which is where the renderer
    places them too.
    """
    spans: list[tuple[str, str]] = []
    open_id: str | None = None
    collected: list[str] = []
    for block_index, block in enumerate(document.blocks):
        if block_index and open_id is not None:
            collected.append("\n\n")
        if block.heading_level is not None and open_id is not None:
            collected.append(_heading_prefix(block.heading_level))
        for fragment in block.fragments:
            for comment_id in fragment.start_comment_ids:
                assert open_id is None, "generated ranges should not nest"
                open_id = comment_id
                collected = []
            collected.append(fragment.text)
            for comment_id in fragment.end_comment_ids:
                assert open_id == comment_id, "a range should close where it opened"
                spans.append((comment_id, "".join(collected)))
                open_id = None
                collected = []
    assert open_id is None, "every generated range should close"
    return spans


def assert_preserves_source_text(document: DocumentModel, rendered: str) -> None:
    """Assert the renderer preserves source text, order, and block structure.

    Only documented formatting may be added: a heading prefix, the blank line
    joining blocks, and the range markers with their comment references.
    Removing exactly those must leave the source text itself.
    """
    parts = rendered.split("\n\n")
    assert len(parts) == len(document.blocks), "each block should render as its part"
    stripped = _strip_range_markers(rendered)
    for comment in document.comments:
        assert stripped.count(comment.body) == 1, (
            "each comment reference should appear exactly once"
        )
        stripped = stripped.replace(comment.body, "")
    assert stripped == _expected_plain_text(document), (
        "rendering should preserve source-text order and content"
    )


def assert_ranges_close_once_each(document: DocumentModel, rendered: str) -> None:
    """Assert each range closes exactly once, after its matching start.

    Ranges are disjoint, so a stack of open positions pairs each close with the
    start it belongs to. Bodies are unique, so matching the model's body
    sequence also proves that no range closes twice and none is dropped.
    """
    opened: list[int] = []
    stack: list[int] = []
    closed: list[str] = []
    for kind, offset, body in _iter_range_tokens(rendered):
        if kind == "open":
            opened.append(offset)
            stack.append(len(opened) - 1)
            continue
        assert stack, "a range closed without a matching open range"
        assert opened[stack.pop()] < offset, "a range closed before its matching start"
        closed.append(body)
    assert not stack, "every opened range should close"
    assert closed == [comment.body for comment in document.comments], (
        "each range should close exactly once, in source order"
    )


def assert_highlights_match_source_ranges(
    document: DocumentModel,
    rendered: str,
) -> None:
    """Assert each highlight encloses exactly its own source span.

    Preserving the text and balancing the markers is not enough on its own: for
    a commented 'A' followed by unowned 'B', both ``{==A==}{>>@a0@<<}B`` and
    ``A{==B==}{>>@a0@<<}`` preserve 'AB' and close one range. Only the first
    highlights the span the model describes, and this comparison is what
    separates them.
    """
    interiors = _highlight_interiors(rendered)
    expected = _expected_highlight_spans(document)
    assert len(interiors) == len(expected), (
        "each generated range should render exactly one highlight"
    )
    for (comment_id, span), interior in zip(expected, interiors, strict=True):
        assert interior == span, (
            f"comment {comment_id} should highlight its own source span, "
            f"not {interior!r}"
        )


def assert_composition_safety(document: DocumentModel, rendered: str) -> None:
    """Assert no construct survives the fragment boundaries that split it.

    Escaping each fragment on its own is not enough: ``<scr`` beside ``ipt>``
    reassembles a live tag, and ``{`` beside ``==`` a live CriticMarkup opener,
    even though neither fragment contains one. The check runs over the final
    rendered output, because that is the only place the reassembled construct
    exists, and it reads that output the way a consumer does.

    The oracle lives in ``tests.support_renderer_oracles`` so the mutation test
    can show it rejects the defects it claims to catch.
    """
    spans = consumed_spans(rendered)
    assert len([chunk for kind, chunk in spans if kind == "range"]) == len(
        document.comments
    ), "each comment should render exactly one consumer-recognized range"
    violation = composition_violation(rendered)
    assert violation is None, f"rendered output is not composition-safe: {violation}"


@given(document=document_models())
def test_render_preserves_source_text_and_range_structure(
    document: DocumentModel,
) -> None:
    """Rendering should preserve order, content, and each range's own span."""
    rendered = render_document(document)

    assert_preserves_source_text(document, rendered)
    assert_ranges_close_once_each(document, rendered)
    assert_highlights_match_source_ranges(document, rendered)


@given(document=document_models(text=composition_text()))
def test_render_is_safe_across_fragment_boundaries(document: DocumentModel) -> None:
    """A construct split across fragments should not survive as live markup."""
    rendered = render_document(document)

    assert_composition_safety(document, rendered)


@example(text=PINNED_ORDER_SENSITIVE)
@given(text=st.lists(_escapable_fragments(), max_size=8).map("".join))
def test_escape_preserves_text_outside_documented_transformations(text: str) -> None:
    r"""Inverting the documented escapes should return the original text.

    The alphabet holds no backslash. Doubling a source backslash and then
    escaping a bracket produces ``\[`` for both ``[`` and ``\[``, so no
    inverse function can be unique over text that already contains an escape
    sequence. The backslash rule is verified by the safety property instead,
    which asks whether the result is inert rather than whether it inverts.
    """
    assert strip_documented_escapes(escape_criticmarkup_text(text)) == text, (
        "escaping should change nothing beyond the documented transformations"
    )


@example(text="héllo, wörld → 字 🙂")
@given(text=st.text(alphabet=INERT_CHARACTERS, max_size=12))
def test_escape_leaves_inert_text_untouched(text: str) -> None:
    """Text containing no escapable construct should be returned unchanged.

    The alphabet excludes every character used by a delimiter, the raw-HTML
    pattern, the Markdown link escapes, and the backslash rule, so no sequence
    formed from it can match one. It includes non-ASCII characters, which
    exercises the Unicode coverage the design document claims. Escaping is
    exact equality here because nothing in the renderer touches such text.
    """
    assert escape_criticmarkup_text(text) == text, (
        "inert text, including non-ASCII text, should pass through unchanged"
    )


@example(text=PINNED_ORDER_SENSITIVE)
@example(text="".join(PINNED_DELIMITERS))
@example(text="".join(PINNED_AMPERSANDS))
@example(text="".join(PINNED_DELIMITERS) + PINNED_ORDER_SENSITIVE)
@example(text=PINNED_BACKSLASH_LINK)
@example(text="".join(PINNED_UNMATCHED_OPENERS))
@given(text=st.lists(_untrusted_fragments(), max_size=8).map("".join))
def test_escape_neutralizes_every_live_construct(text: str) -> None:
    """No delimiter, bracket, entity, or raw HTML tag should stay live.

    This is a consumer-semantics check, not a lexical one. A construct counts
    as guarded only when a CommonMark reader would not act on it, and CommonMark
    treats a character as escaped only when an odd-length backslash run precedes
    it. An earlier form of this check accepted any immediately preceding
    backslash, which passed an active link built from a source backslash; the
    pinned example is that regression.
    """
    _assert_no_live_construct(escape_criticmarkup_text(text))


@given(text=st.lists(_untrusted_fragments(), max_size=8).map("".join))
def test_escape_consumes_exactly_the_raw_html_tags_it_matches(text: str) -> None:
    """Escaping should consume a bracket for each tag matched, and no more.

    This is anchored to the input rather than searched in the output. Searching
    the output would report a false positive: the pattern preserves a tag's
    content verbatim, so a literal '<' inside content can be re-matched by the
    same pattern against the output even though its closing '>' has become the
    ';' of an '&gt;' entity.

    A complete tag is not the only bracket escaping spends. An unclosed
    HTML-block opener at the start of the text is entity-escaped by the separate
    guard, so the expected count is the matched tags' brackets plus one opening
    bracket per opener guarded. ``guarded_opener_count`` restates that rule
    independently, so dropping an opener from the implementation fails here
    rather than redefining the expectation.
    """
    escaped = escape_criticmarkup_text(text)
    matched = RAW_HTML_TAG.findall(text)
    guarded = guarded_opener_count(text)

    for tag in matched:
        assert tag not in escaped, f"{tag!r} survived escaping intact"

    expected_open_brackets = text.count("<") - len(matched) - guarded
    expected_close_brackets = text.count(">") - len(matched)
    assert escaped.count("<") == expected_open_brackets, (
        "only a matched tag's or a guarded opener's bracket should be escaped"
    )
    assert escaped.count(">") == expected_close_brackets, (
        "only a matched tag's closing bracket should be entity-escaped"
    )
