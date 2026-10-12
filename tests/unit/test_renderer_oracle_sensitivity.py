"""Mutation-test the renderer property oracles.

A property test is only as valuable as its sensitivity. An oracle that passes
for every input proves nothing about the renderer, and a review cannot tell a
strong oracle from a weak one by reading the assertions alone. Each test here
deliberately breaks one renderer behaviour and shows that the oracle rejects the
resulting output, so its sensitivity is demonstrated rather than claimed.

The mutations are applied with ``monkeypatch`` and are confined to the renderer
module or the oracle's own dependencies, so they cannot leak between tests.
"""

from __future__ import annotations

import pytest

from docx_comment_extractor.models import Block, Comment, DocumentModel, Fragment
from docx_comment_extractor.renderer import render_document
from tests.support_renderer_oracles import (
    apply_boundary_insensitive_escaping,
    apply_generation_3_html_block_guard,
    apply_generation_3_raw_html_pattern,
    apply_leading_only_html_block_guard,
    apply_lf_only_indent_html_block_guard,
    apply_missing_backslash_rule,
    apply_per_fragment_escaping,
    composition_violation,
    consumed_references,
    consumed_spans,
)
from tests.unit.test_renderer_properties import assert_highlights_match_source_ranges

# A tag and a delimiter deliberately split across run boundaries, the shapes
# Word produces when formatting changes mid-token.
SPLIT_TAG_FRAGMENTS = ("<scr", "ipt>alert(1)</scr", "ipt", ">")
SPLIT_DELIMITER_FRAGMENTS = ("{", "==", "x", "==", "}")
BACKSLASH_LINK_FRAGMENT = r"\[run\](https://example.org)"


def _document(
    texts: tuple[str, ...],
    *,
    comments: int = 0,
    range_length: int | None = None,
    bodies: tuple[str, ...] = (),
) -> DocumentModel:
    """Build a one-block document over the given fragment texts.

    ``range_length`` bounds the first comment's range. The default spans every
    fragment, which is the shape that leaves uncommented prose outside the
    range; a shorter range is what puts a boundary in the middle of a construct
    the fragments reassemble.

    ``bodies`` overrides the default ``@aN@`` body, so a probe can put reactive
    text where the renderer escapes the comment reference.
    """
    last = len(texts) - 1 if range_length is None else range_length - 1
    fragments = tuple(
        Fragment(
            text=text,
            start_comment_ids=("c0",) if comments and index == 0 else (),
            end_comment_ids=("c0",) if comments and index == last else (),
        )
        for index, text in enumerate(texts)
    )
    return DocumentModel(
        blocks=(Block(kind="paragraph", fragments=fragments),),
        comments=tuple(
            Comment(
                comment_id=f"c{index}",
                author=None,
                body=bodies[index] if index < len(bodies) else f"@a{index}@",
            )
            for index in range(comments)
        ),
    )


def test_healthy_renderer_passes_the_oracle() -> None:
    """The oracle should accept the current renderer on every probe shape.

    Without this control, a mutation test could pass because the oracle rejects
    everything, which would make the other cases meaningless.
    """
    for texts in (
        SPLIT_TAG_FRAGMENTS,
        SPLIT_DELIMITER_FRAGMENTS,
        (BACKSLASH_LINK_FRAGMENT,),
    ):
        rendered = render_document(_document(texts))
        assert composition_violation(rendered) is None, (
            f"the current renderer was rejected: {rendered!r}"
        )


def test_oracle_detects_per_fragment_escaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escaping each fragment alone should be caught as unsafe output.

    This is the defect the contiguous-grouping fix removed: neither ``<scr`` nor
    ``ipt>`` is a tag, but the escaped fragments reassemble one.
    """
    apply_per_fragment_escaping(monkeypatch)

    tag_rendered = render_document(_document(SPLIT_TAG_FRAGMENTS))
    tag_violation = composition_violation(tag_rendered)
    assert tag_violation is not None, (
        f"a reassembled live tag was not detected in {tag_rendered!r}"
    )
    assert "raw HTML" in tag_violation

    delimiter_rendered = render_document(_document(SPLIT_DELIMITER_FRAGMENTS))
    delimiter_violation = composition_violation(delimiter_rendered)
    assert delimiter_violation is not None, (
        f"a reassembled live opener was not detected in {delimiter_rendered!r}"
    )
    assert "w==" in delimiter_violation or "{==" in delimiter_violation


def test_oracle_detects_boundary_insensitive_grouping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Grouping across a comment boundary should be caught, not accepted.

    The escape guard has to ignore adjacent *logical* text, but the renderer's
    own markers are inserted between fragments at a boundary, so a construct
    cannot span one. Grouping regardless escapes text the renderer would have
    kept separate, and on a commented fragment the markers are lost with it.

    The shape matters. A commented fragment holding only half a delimiter is
    one the healthy renderer and the mutant agree on, because the range the
    mutant fails to emit is still consumed by the anchored reader as text. The
    probe that catches the mutant is a comment whose whole extent is the first
    half of a construct followed by uncommented text: the healthy renderer
    closes the range before ``ipt>``, so nothing reassembles, while the mutant
    escapes the block as one unit and never emits the closing markers at all.

    The observable defect is the range itself. A consumer sees a swallowed
    opener where the model described a comment, so the count of
    consumer-recognized ranges drops, and the composition oracle reports the
    live opener that is left behind.
    """
    apply_boundary_insensitive_escaping(monkeypatch)

    document = _document(("<scr", "ipt>"), comments=1, range_length=1)
    rendered = render_document(document)
    ranges = [chunk for kind, chunk in consumed_spans(rendered) if kind == "range"]
    assert len(ranges) < len(document.comments), (
        f"the mutant should lose a consumer-recognized range: {rendered!r}"
    )
    assert composition_violation(rendered) is not None, (
        f"a swallowed range was not detected: {rendered!r}"
    )


def test_oracle_detects_missing_backslash_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""A source backslash that cancels the added escape should be caught.

    With the doubling rule removed, ``\[`` renders as ``\\[``, and a
    CommonMark reader sees an even backslash run followed by an active bracket.
    """
    apply_missing_backslash_rule(monkeypatch)

    rendered = render_document(_document((BACKSLASH_LINK_FRAGMENT,)))
    violation = composition_violation(rendered)
    assert violation is not None, (
        f"an active bracket after an even backslash run was not detected: {rendered!r}"
    )
    assert "bracket" in violation


def test_consumer_split_ignores_delimiters_inside_a_range() -> None:
    """Delimiter text inside a consumed range is content, not markup.

    A substring search over the whole output would count the prose ``{`` in
    ``{=={==}{>>...<<}`` as a live opener. An anchored reader consumes the whole
    range, so the oracle must not report it. This is the false positive that
    motivated modelling consumption instead of counting occurrences.
    """
    rendered = "{=={==}{>>@a1@<<}"
    spans = consumed_spans(rendered)
    assert [kind for kind, _chunk in spans] == ["range"]
    assert composition_violation(rendered) is None


def test_oracle_detects_indented_and_case_insensitive_openers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-repair guard's misses should be rejected, not accepted.

    The old guard acted only at string position zero and matched block tag
    names case-sensitively. CommonMark does neither: a block starts at any line
    start, allows up to three leading spaces, and matches the names
    case-insensitively, so every shape below opened a block that swallowed the
    renderer's own markers. The oracle and the renderer once shared this blind
    spot, which is why the property passed while excluding nothing; this
    control keeps the two from drifting back into agreement on the defect.
    """
    apply_leading_only_html_block_guard(monkeypatch)

    for text in (" <script", "<SCRIPT", "safe\n<script", "safe\n<!--"):
        rendered = render_document(_document((text,)))
        assert composition_violation(rendered) is not None, (
            f"a missed HTML-block opener was not detected in {rendered!r}"
        )


def test_oracle_detects_carriage_return_and_container_openers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The LF-only, indent-only guard's misses should be rejected, not accepted.

    The guard this replaces anchored with ``(?m:^)``, which Python matches after
    a line feed and nowhere else, and permitted only three spaces of indent. Two
    shapes therefore stayed live: an opener whose line ended at a carriage
    return, and one reached through a blockquote or list marker, which promotes
    the opener to a block inside the container. The shared oracle had the same
    blind spot, so the properties could not have caught either. This control
    pins the oracle's sensitivity to both.
    """
    apply_lf_only_indent_html_block_guard(monkeypatch)

    # Each of these is live for the parser, reached the pre-repair guard, and
    # was left alone by it. A bare CR is the distinguishing case for the anchor:
    # a CRLF line already ends in a line feed, so the old ``(?m:^)`` matched it
    # and it cannot show the defect.
    misses = (
        "safe\r<script",
        "safe\r<!--",
        "> <script",
        "> <!--",
        "- <script",
        "1. <script",
        "> - > <script",
    )
    for text in misses:
        rendered = render_document(_document((text,)))
        assert composition_violation(rendered) is not None, (
            f"a missed HTML-block opener was not detected in {rendered!r}"
        )


def test_healthy_renderer_guards_carriage_return_and_container_openers() -> None:
    """The control: the current guard neutralizes both shapes.

    Without this, the mutation above could pass because the oracle rejects
    every input rather than because the pre-repair guard missed these.
    """
    for text in (
        "safe\r<script",
        "safe\r\n<script",
        "safe\r<!--",
        "safe\r\n<!--",
        "> <script",
        "> <!--",
        "- <script",
        "1. <script",
        "> - > <script",
    ):
        rendered = render_document(_document((text,)))
        assert composition_violation(rendered) is None, (
            f"the current renderer produced unsafe output: {rendered!r}"
        )


def test_reference_check_detects_live_markup_in_a_comment_body() -> None:
    """A live construct in a comment reference should be caught, not skipped.

    The reference is escaped by the same function as document text, so a body
    can carry a reassembled opener just as a fragment can. An earlier form of
    ``consumed_spans`` reported only the highlight interior, so a body was
    discarded before any check saw it and the composition property could not
    have caught a defect there.
    """
    for body in ("a {== b", "see [link", "safe\n<script"):
        rendered = f"{{==x==}}{{>>{body}<<}}"
        assert consumed_references(rendered) == [body], (
            "the oracle should report the reference it withholds from the interior"
        )
        violation = composition_violation(rendered)
        assert violation is not None, (
            f"live markup in a comment body was not detected: {rendered!r}"
        )


def test_healthy_renderer_escapes_reactive_comment_bodies() -> None:
    """The control for the reference check: real escaping leaves it clean.

    Without this, the check above could pass because the oracle rejects every
    body rather than because a defect is present.
    """
    for body in ("a {== b", "see [link", "safe\n<script"):
        rendered = render_document(_document(("hi",), bodies=(body,)))
        assert composition_violation(rendered) is None, (
            f"the current renderer produced unsafe output: {rendered!r}"
        )


def test_oracle_detects_displaced_highlight_extents() -> None:
    """A highlight over the wrong span should be caught, not accepted.

    Preserving the text and balancing markers is not enough: for a commented
    ``A`` followed by unowned ``B``, both ``{==A==}{>>@a0@<<}B`` and
    ``A{==B==}{>>@a0@<<}`` preserve ``AB`` and close one range. Only the first
    highlights the model's span, so the extent oracle is what separates them.
    This pins that the oracle is sensitive to the displacement rather than
    merely to a missing marker.
    """
    document = _document(("A", "B"), comments=1, range_length=1)

    correct = "{==A==}{>>@a0@<<}B"
    displaced = "A{==B==}{>>@a0@<<}"
    assert_highlights_match_source_ranges(document, correct)

    with pytest.raises(AssertionError, match="should highlight its own source span"):
        assert_highlights_match_source_ranges(document, displaced)


# The continuation shapes the pre-repair guard left live: a container opened on
# one line whose opener arrives on a later one. Every entry is live for the
# parser, was reachable through ``render_document``, and slipped past the
# column-bounded, per-line guard.
CONTINUATION_OPENER_MISSES = (
    "10. item\n    <script",
    "123456789. item\n           <script",
    "- outer\n  - inner\n    <script",
    "> 10. item\n>     <script",
    "10. item\n    > <!--",
    "- item\n\n    <script",
    "10. item\r    <script",
    "10. item\r\n    <script",
    "10. item\r    <!--",
    "10. item\r\n    <!--",
    "> 10. item\r>     <script",
)


def test_oracle_detects_continuation_openers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""The stateless guard's continuation misses should be rejected, not accepted.

    The guard this replaces had the right anchor and folded away every
    container *on the line carrying the marker*. What no per-line rule can do is
    follow a container onto a later line: CommonMark retains it across the break
    (and across a blank line), and the continuation line's indentation is
    measured from the container's content column rather than from the document
    margin. So ``"10. item\n    <script"`` opened a real ``html_block`` while
    the guard inspected that line alone and saw four spaces of indent.

    This is the paired mutation for the continuation repair. The oracle has to
    report every one of these, or the safety property passes over a live block.
    """
    apply_generation_3_html_block_guard(monkeypatch)

    for text in CONTINUATION_OPENER_MISSES:
        rendered = render_document(_document((text,)))
        assert composition_violation(rendered) is not None, (
            f"a missed continuation opener was not detected in {rendered!r}"
        )


def test_healthy_renderer_guards_continuation_openers() -> None:
    """The control: the repaired guard neutralizes every continuation shape.

    Without this, the mutation above could pass because the oracle rejects every
    input rather than because the pre-repair guard missed these.
    """
    for text in CONTINUATION_OPENER_MISSES:
        rendered = render_document(_document((text,)))
        assert composition_violation(rendered) is None, (
            f"the current renderer produced unsafe output: {rendered!r}"
        )


def test_oracle_detects_continuation_openers_split_across_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A continuation opener split across Word runs should still be caught.

    The container opens in one fragment and the opener sits on a later line of
    the next, so the guard only sees the assembled form once the renderer has
    joined them. This is the run-boundary form of the mutation above.
    """
    apply_generation_3_html_block_guard(monkeypatch)

    for fragments in (
        ("10. item\n", "    <script"),
        ("10. item\n   ", " <script"),
        ("> 10. item\n>", "     <script"),
        ("10. item\r", "    <!--"),
    ):
        rendered = render_document(_document(fragments))
        assert composition_violation(rendered) is not None, (
            f"a missed continuation opener was not detected in {rendered!r}"
        )


def test_healthy_renderer_guards_continuation_openers_split_across_runs() -> None:
    """The control for the run-boundary form of the continuation repair."""
    for fragments in (
        ("10. item\n", "    <script"),
        ("10. item\n   ", " <script"),
        ("> 10. item\n>", "     <script"),
        ("10. item\r", "    <!--"),
    ):
        rendered = render_document(_document(fragments))
        assert composition_violation(rendered) is None, (
            f"the current renderer produced unsafe output: {rendered!r}"
        )


def test_oracle_detects_a_mid_line_processing_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-repair raw-HTML pattern's mid-line PI miss should be rejected.

    ``RAW_HTML_PATTERN``'s first character class held letters, ``!`` and ``/``,
    so a processing instruction beginning ``<?`` never matched the mid-line tag
    rule. At a line start the block-opener guard still caught one, which is why
    the gap only showed inside a line: a fragment reading ``"a <?php ?>"``
    escaped to itself and a reader saw ``html_inline``.
    """
    apply_generation_3_raw_html_pattern(monkeypatch)

    for text in ("a <?php ?>", "safe <?php echo 1; ?> safe", "a <? x ?>"):
        rendered = render_document(_document((text,)))
        assert composition_violation(rendered) is not None, (
            f"a live mid-line processing instruction was not detected: {rendered!r}"
        )


def test_healthy_renderer_guards_a_mid_line_processing_instruction() -> None:
    """The control: the repaired pattern neutralizes a mid-line PI.

    The line-start forms are deliberately included. They were already guarded
    before the repair, so a control that only exercised them would pass against
    the mutant and prove nothing about the mid-line rule.
    """
    for text in ("a <?php ?>", "safe <?php echo 1; ?> safe", "<?php ?>", "a <? x ?>"):
        rendered = render_document(_document((text,)))
        assert composition_violation(rendered) is None, (
            f"the current renderer produced unsafe output: {rendered!r}"
        )
