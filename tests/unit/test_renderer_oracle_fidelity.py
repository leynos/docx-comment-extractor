"""Calibrate the raw-HTML oracle against a real CommonMark parser.

The renderer properties assert that no live construct survives escaping. An
oracle for that claim is only as good as its agreement with an actual consumer:
a pattern that is too loose rejects safe output, and one that is too narrow
accepts output a reader would parse as markup. This test pins the oracle to
``markdown-it-py`` rather than to my reading of the specification.

``markdown-it-py`` is declared in the development dependency group, not the
production one, so the runtime dependency set does not grow for a test-only
need. It is imported unconditionally: the calibration is required evidence for
the boundary oracle, and a skip would let it disappear from a passing run
without any signal.
"""

from __future__ import annotations

import pytest
from markdown_it import MarkdownIt

from tests.support_renderer_oracles import LIVE_RAW_HTML

# Each case pairs a snippet with the classification a CommonMark reader gives
# it. The corpus covers every raw-HTML shape the specification recognizes, the
# near-misses an over-eager pattern wrongly accepts, and the residues a real
# escape leaves behind.
CALIBRATION_CASES: tuple[tuple[str, bool], ...] = (
    # Recognized raw HTML.
    ("<script>alert(1)</script>", True),
    ("<b>", True),
    ("</div>", True),
    ("<img src=x>", True),
    ('<a href="x">', True),
    ("<a href='x'>", True),
    ('<a href="x" target="_blank">', True),
    ("<br/>", True),
    ("<br />", True),
    ("<b >", True),
    ("<b/>x", True),
    ("<a href=''>", True),
    ("<a b=c>", True),
    ("<x-custom>", True),
    ("<!-- comment -->", True),
    ("<?php ?>", True),
    ("<![CDATA[x]]>", True),
    ("<!DOCTYPE html>", True),
    # CommonMark treats an unclosed inline comment as raw HTML to end of input.
    ("<!--", True),
    # An HTML block may be indented by up to three spaces at the start of a
    # line, and the block tag names are matched case-insensitively. Both were
    # missed by an earlier form of the oracle, which classified them as inert
    # while a real reader opened a block that swallowed the following markup.
    (" <script", True),
    ("  <script", True),
    ("   <script", True),
    ("<SCRIPT", True),
    ("<Script", True),
    (" <DIV", True),
    ("</DIV", True),
    ("<H1", True),
    ("safe\n<script", True),
    ("safe\n <script", True),
    ("safe\n   <script", True),
    ("safe\n<!--", True),
    ("safe\n<!--x", True),
    ("safe\n<DIV", True),
    # The shape that does the damage: an unmatched opener at a line start
    # followed by the renderer's own markers.
    ("<script\n{==x==}{>>note<<}", True),
    (" <script\n{==x==}{>>note<<}", True),
    ("safe\n <script\n{==x==}{>>@a0@<<}", True),
    # Near-misses that must not match.
    # Four spaces is indented code, not an HTML block; a tab is not a space.
    ("    <script", False),
    ("\t<script", False),
    ("safe\n    <script", False),
    # A declaration still needs its uppercase letter.
    (" <!x", False),
    ("safe\n <!", False),
    ("&lt;script", False),
    ("<a~>", False),
    ("<scr", False),
    ("a<scr", False),
    ("<3>", False),
    ("< b>", False),
    ("<a href=>", False),
    ("<x:y>", False),
    # Residues the renderer's own escaping leaves behind.
    ("&lt;script&gt;", False),
    ("<script&gt;", False),
    ('<a href="x"&gt;', False),
    ('&lt;!--<a href="x"&gt;~\\\\>', False),
    ("&lt;!--<script&gt;alert(1)&lt;/script&gt;~\\\\>", False),
    ("{==\\{====}{>>@a0@<<}", False),
    ("&lt;", False),
    ("x&gt;", False),
)


def _live_html_tokens(markdown: MarkdownIt, text: str) -> list[str]:
    """Collect every raw-HTML token, descending into inline children.

    ``md.parse`` returns block tokens, and inline HTML lives in the children of
    ``inline`` tokens. A top-level scan alone misses most of the corpus, so the
    walk has to recurse.
    """
    found: list[str] = []
    stack = list(markdown.parse(text))
    while stack:
        token = stack.pop()
        if token.type in {"html_inline", "html_block"}:
            found.append(token.content)
        children = token.children
        if children:
            stack.extend(children)
    return found


@pytest.mark.parametrize(("text", "is_live"), CALIBRATION_CASES)
def test_oracle_agrees_with_commonmark(text: str, *, is_live: bool) -> None:
    """The oracle's verdict should match a real CommonMark parser's."""
    markdown = MarkdownIt("commonmark")
    parser_verdict = bool(_live_html_tokens(markdown, text))
    oracle_verdict = bool(LIVE_RAW_HTML.search(text))
    assert oracle_verdict == parser_verdict == is_live, (
        f"{text!r}: oracle={oracle_verdict} parser={parser_verdict} expected={is_live}"
    )


def test_oracle_rejects_every_escaping_residue() -> None:
    """Escaped output should contain no raw HTML a parser would recognize.

    This is the end-to-end form of the calibration: it takes real escaping
    output rather than hand-written snippets, so it also covers whatever the
    escape table does to a tag that straddles the raw-HTML pattern.
    """
    from docx_comment_extractor.renderer import escape_criticmarkup_text

    markdown = MarkdownIt("commonmark")
    samples = (
        "<script>alert(1)</script>",
        '<a href="x">',
        "<!--",
        "</div>",
        "<b>",
        "<img src=x>",
        r'<!--<a href="x">~\>',
        r"<!--<script>alert(1)</script>~\>",
    )
    for sample in samples:
        escaped = escape_criticmarkup_text(sample)
        assert not _live_html_tokens(markdown, escaped), (
            f"{sample!r} escaped to live markup {escaped!r}"
        )


# The line-start openers an earlier form of the guard left live, including the
# shapes that reassemble across a Word run boundary and the one that swallows
# the renderer's own markers. These assert on the parser's verdict over final
# output rather than on escape-string equality, because equality to a wrong
# expectation passed while a real reader still opened a block.
PINNED_LINE_START_OPENERS: tuple[str, ...] = (
    " <script",
    "  <script",
    "   <script",
    "<SCRIPT",
    "<Script",
    " <DIV",
    "</DIV",
    "<H1",
    "safe\n<script",
    "safe\n <script",
    "safe\n   <script",
    "safe\n<!--",
    "safe\n<!--x",
    "safe\n<DIV",
    "<script\n{==x==}{>>note<<}",
    " <script\n{==x==}{>>note<<}",
    "safe\n <script\n{==x==}{>>@a0@<<}",
)


@pytest.mark.parametrize("text", PINNED_LINE_START_OPENERS)
def test_escaping_neutralizes_indented_and_case_insensitive_openers(
    text: str,
) -> None:
    """Indented, uppercase, and post-line-break openers should be neutralized.

    A CommonMark HTML block starts at a line start with up to three leading
    spaces, and its block tag names are case-insensitive. Refusing to
    neutralize any of those shapes leaves a live ``html_block`` that runs to the
    next blank line, which is where the renderer's own CriticMarkup markers sit.
    """
    from docx_comment_extractor.renderer import escape_criticmarkup_text

    markdown = MarkdownIt("commonmark")
    escaped = escape_criticmarkup_text(text)
    assert not _live_html_tokens(markdown, escaped), (
        f"{text!r} escaped to live markup {escaped!r}"
    )


# A construct split across Word runs reassembles before escaping sees it. Each
# case is the ordered pair of fragments the extractor would hand over; the
# renderer escapes the joined text, so the pair must be neutralized as one.
PINNED_SPLIT_OPENERS: tuple[tuple[str, ...], ...] = (
    (" <scr", "ipt"),
    ("  <SCR", "IPT"),
    ("safe\n", " <script"),
    ("<!--", "\n<script"),
    ("   <!", "--"),
)


@pytest.mark.parametrize(("fragments"), PINNED_SPLIT_OPENERS)
def test_escaping_neutralizes_openers_reassembled_across_runs(
    fragments: tuple[str, ...],
) -> None:
    """An opener split across Word runs should be neutralized once rejoined."""
    from docx_comment_extractor.renderer import escape_criticmarkup_text

    markdown = MarkdownIt("commonmark")
    escaped = escape_criticmarkup_text("".join(fragments))
    assert not _live_html_tokens(markdown, escaped), (
        f"{fragments!r} reassembled to live markup {escaped!r}"
    )


def test_oracle_guards_every_opener_escaping_neutralizes() -> None:
    """The oracle must detect the shapes the renderer actually guards.

    The oracle and the renderer once shared the same blind spot, so passing
    properties excluded nothing. This walks the pinned openers through the
    oracle to keep the two from drifting back into agreement on a defect.
    """
    for text in PINNED_LINE_START_OPENERS:
        assert LIVE_RAW_HTML.search(text) is not None, (
            f"the oracle treats the live opener {text!r} as inert"
        )
