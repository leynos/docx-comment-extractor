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

import typing as typ

from docx_comment_extractor.models import Block, Comment, DocumentModel, Fragment
from docx_comment_extractor.renderer import render_document
from tests.support_renderer_oracles import (
    apply_boundary_insensitive_escaping,
    apply_missing_backslash_rule,
    apply_per_fragment_escaping,
    composition_violation,
    consumed_spans,
)

if typ.TYPE_CHECKING:
    import pytest

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
) -> DocumentModel:
    """Build a one-block document over the given fragment texts.

    ``range_length`` bounds the first comment's range. The default spans every
    fragment, which is the shape that leaves uncommented prose outside the
    range; a shorter range is what puts a boundary in the middle of a construct
    the fragments reassemble.
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
            Comment(comment_id=f"c{index}", author=None, body=f"@a{index}@")
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
