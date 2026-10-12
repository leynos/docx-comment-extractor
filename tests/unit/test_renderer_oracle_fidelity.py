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
    # A line ends at a carriage return as well as at a line feed, and CommonMark
    # ends one at a bare CR. The extractor renders a ``w:br`` or ``w:cr`` as
    # ``"\n"``, but a numeric character reference reaches the renderer as a real
    # CR, so this shape is reachable and an LF-only anchor misses it.
    ("\r<script", True),
    ("\r\n<script", True),
    ("safe\r<script", True),
    ("safe\r\n<script", True),
    ("safe\r<!--", True),
    ("safe\r\n<!--", True),
    ("safe\r<div", True),
    ("safe\r\n<DIV", True),
    ("safe\r<script\n{==x==}{>>note<<}", True),
    # An opener inside a blockquote or list item is promoted to an HTML block
    # within the container, so the container prefix must be folded away too. A
    # marker may carry up to three spaces of its own indentation; a blockquote
    # marker may swallow up to four whitespace characters and needs none; a list
    # marker needs one to four to separate itself from its content.
    ("> <script", True),
    ("> <SCRIPT", True),
    ("> <H1", True),
    ("> <!--", True),
    ("> <div", True),
    ("  > <script", True),
    ("   > <script", True),
    (">\t<script", True),
    (">  <script", True),
    (">    <script", True),
    (">> <script", True),
    ("- <script", True),
    ("- <!--", True),
    ("* <div", True),
    ("+ <pre", True),
    ("1. <script", True),
    ("1) <!--", True),
    (" - <script", True),
    ("  - <script", True),
    ("   - <script", True),
    ("-   <script", True),
    ("1.\t<script", True),
    ("> - <script", True),
    ("- > <script", True),
    ("> - > <script", True),
    ("> <script\n{==x==}{>>note<<}", True),
    ("- safe\n- <!--", True),
    # Near-misses that must not match.
    # Four spaces is indented code, not an HTML block; a tab is not a space.
    ("    <script", False),
    ("\t<script", False),
    ("safe\n    <script", False),
    ("\r    <script", False),
    ("\r\t<script", False),
    ("safe\r    <script", False),
    # A tab in the indent position is four columns, so these are indented code
    # rather than a container holding an opener.
    ("    > <script", False),
    ("\t> <script", False),
    ("safe\n    > <script", False),
    # A blockquote marker folds at most four whitespace characters, so a fifth
    # is content indentation -- four columns of it -- and makes indented code.
    (">     <script", False),
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
    # Indentation.
    " <script",
    "  <script",
    "   <script",
    # Case.
    "<SCRIPT",
    "<Script",
    " <DIV",
    "</DIV",
    "<H1",
    # A line feed.
    "safe\n<script",
    "safe\n <script",
    "safe\n   <script",
    "safe\n<!--",
    "safe\n<!--x",
    "safe\n<DIV",
    # A carriage return, which ends a line for CommonMark just as a line feed
    # does. The extractor emits "\n" for a Word break, but a numeric character
    # reference survives XML parsing as a real CR, so this is reachable.
    "\r<script",
    "\r\n<script",
    "safe\r<script",
    "safe\r\n<script",
    "safe\r<!--",
    "safe\r\n<!--",
    "safe\r<div",
    "safe\r\n<DIV",
    # A container prefix, which promotes the opener to a block inside the
    # container just as indentation does.
    "> <script",
    "> <SCRIPT",
    "> <H1",
    "> <!--",
    "> <div",
    ">\t<script",
    ">  <script",
    ">    <script",
    ">> <script",
    "  > <script",
    "   > <script",
    "- <script",
    "- <!--",
    "* <div",
    "+ <pre",
    "1. <script",
    "1) <!--",
    "1.\t<script",
    " - <script",
    "  - <script",
    "   - <script",
    "-   <script",
    "> - <script",
    "- > <script",
    "> - > <script",
    # The shapes that do the damage: an opener followed by the renderer's own
    # markers, which is what an unclosed block would swallow.
    "<script\n{==x==}{>>note<<}",
    " <script\n{==x==}{>>note<<}",
    "safe\n <script\n{==x==}{>>@a0@<<}",
    "> <script\n{==x==}{>>note<<}",
    "safe\r<script\n{==x==}{>>note<<}",
    "- safe\n- <!--",
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

    A line also ends at a carriage return, and a blockquote or list container
    reaches the opener through its markers rather than through indentation.
    Both were missed by the guard this test replaced.
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
    """An opener split across Word runs should be neutralized once rejoined.

    This calls the escape function on the joined pair directly, so it tests the
    escape rule rather than the renderer's fragment grouping. The grouping is
    covered separately by
    ``test_render_document_neutralizes_openers_split_across_runs`` below; this
    test would keep passing if the renderer handed the escape function one
    fragment at a time.
    """
    from docx_comment_extractor.renderer import escape_criticmarkup_text

    markdown = MarkdownIt("commonmark")
    escaped = escape_criticmarkup_text("".join(fragments))
    assert not _live_html_tokens(markdown, escaped), (
        f"{fragments!r} reassembled to live markup {escaped!r}"
    )


# The same reassembly driven through the public renderer, so the fragment
# grouping is exercised rather than assumed. ``render_document`` escapes a run
# of contiguous fragments in one buffer, which is what makes a construct
# spanning a Word run boundary visible to the guard. Each pair here is split
# *inside* the prefix rather than inside the opener, so the escape function
# never sees the assembled form unless the grouping actually happened.
PINNED_SPLIT_PREFIXES: tuple[tuple[str, ...], ...] = (
    (">", " <script"),
    ("> ", "<script"),
    ("-", " <script"),
    ("1.", " <script"),
    ("safe\n>", " <script"),
    ("safe\r", "<script"),
    ("   >", " <script"),
    ("> -", " > <script"),
)


@pytest.mark.parametrize(("fragments"), PINNED_SPLIT_PREFIXES)
def test_render_document_neutralizes_openers_split_across_runs(
    fragments: tuple[str, ...],
) -> None:
    """The renderer should neutralize an opener split across Word runs.

    A Word paragraph can split a single visual line across several runs at any
    point, including inside a blockquote or list marker. The guard only sees
    the opener once the renderer has joined the run of contiguous fragments, so
    this is the grouping contract rather than the escape rule.
    """
    from docx_comment_extractor.models import Block, DocumentModel, Fragment
    from docx_comment_extractor.renderer import render_document

    markdown = MarkdownIt("commonmark")
    document = DocumentModel(
        blocks=(Block("paragraph", tuple(Fragment(text) for text in fragments)),),
        comments=(),
    )
    rendered = render_document(document)
    assert not _live_html_tokens(markdown, rendered), (
        f"{fragments!r} rendered to live markup {rendered!r}"
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


def test_oracle_shares_the_line_start_condition_it_claims_to_judge() -> None:
    """The oracle's line-start anchor must not be a bare ``(?m:^)``.

    Python's multiline ``^`` matches after a line feed and nowhere else, so an
    anchor written that way classifies a carriage-return-terminated line and a
    container-prefixed opener as inert. The renderer properties would then pass
    over live markup. This asserts the anchor sees all three line endings and a
    folded container prefix, rather than only the shape the oracle's own corpus
    happens to contain.
    """
    live = (
        "safe\r<script",
        "safe\r\n<script",
        "safe\n> <script",
        "> - <script",
        "safe\r> <script",
    )
    for text in live:
        assert LIVE_RAW_HTML.search(text) is not None, (
            f"the oracle treats the live opener {text!r} as inert"
        )
